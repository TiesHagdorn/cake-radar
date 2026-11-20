import { DefineFunction, Schema, SlackFunction } from "deno-slack-sdk/mod.ts";
import type { SlackAPIClient } from "deno-slack-sdk/types.ts";
import { ProcessedMessagesDatastore } from "../datastores/processed_messages.ts";
import {
  assessCertainty,
  type ChatCompletion,
  findCakeWords,
  formatVotes,
  isClassifierYes,
  judgePanel,
  openAIChatCompletion,
  type OperationalAlert,
  rescueJudge,
} from "./classifier.ts";
import { loadSettings, type Settings } from "./config.ts";

const DEDUP_TTL_SECONDS = 7 * 24 * 60 * 60;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const OPENAI_IMAGE_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
]);

export const DetectTreatFunctionDefinition = DefineFunction({
  callback_id: "detect_treat_function",
  title: "Detect treat",
  description: "Classify a message as a treat offering and alert if it is one",
  source_file: "functions/detect_treat_function.ts",
  input_parameters: {
    properties: {
      channel_id: { type: Schema.slack.types.channel_id },
      message_ts: { type: Schema.types.string },
      channel_type: { type: Schema.types.string },
      text: { type: Schema.types.string },
      user_id: { type: Schema.slack.types.user_id },
    },
    required: ["channel_id", "channel_type", "message_ts"],
  },
  output_parameters: {
    properties: {
      outcome: { type: Schema.types.string },
    },
    required: ["outcome"],
  },
});

type SlackFile = {
  mimetype?: string;
  size?: number;
  url_private_download?: string;
  url_private?: string;
};

type SlackMessage = { ts: string; text?: string; files?: SlackFile[] };

async function fetchTopLevelMessage(
  client: SlackAPIClient,
  channelId: string,
  ts: string,
): Promise<SlackMessage | undefined> {
  const history = await client.conversations.history({
    channel: channelId,
    latest: ts,
    inclusive: true,
    limit: 1,
  });
  const message = history.ok ? history.messages?.[0] : undefined;
  return message?.ts === ts ? message : undefined;
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function downloadFirstImage(
  files: SlackFile[],
  token: string,
): Promise<string[]> {
  for (const file of files) {
    const url = file.url_private_download ?? file.url_private;
    const mimetype = file.mimetype ?? "";
    if (!url || !OPENAI_IMAGE_TYPES.has(mimetype)) continue;
    if ((file.size ?? 0) > MAX_IMAGE_BYTES) continue;
    try {
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const contentType = response.headers.get("content-type") ?? "";
      if (!response.ok || !contentType.startsWith("image/")) {
        console.warn(
          `Image download returned ${response.status} ${contentType}`,
        );
        await response.body?.cancel();
        continue;
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      return [`data:${mimetype};base64,${toBase64(bytes)}`];
    } catch (error) {
      console.error(`Failed to download Slack image: ${error}`);
    }
  }
  return [];
}

async function claimMessage(
  client: SlackAPIClient,
  id: string,
): Promise<boolean> {
  const existing = await client.apps.datastore.get<
    typeof ProcessedMessagesDatastore.definition
  >({ datastore: ProcessedMessagesDatastore.name, id });
  if (existing.ok && existing.item?.id) return false;
  await client.apps.datastore.put<
    typeof ProcessedMessagesDatastore.definition
  >({
    datastore: ProcessedMessagesDatastore.name,
    item: {
      id,
      expires_at: Math.floor(Date.now() / 1000) + DEDUP_TTL_SECONDS,
    },
  });
  return true;
}

function operationalAlerter(
  client: SlackAPIClient,
  settings: Settings,
): OperationalAlert {
  return async (kind, context) => {
    if (!settings.operationalAlertChannelId) {
      console.error(
        "OpenAI operational alert suppressed: no OPERATIONAL_ALERT_CHANNEL_ID configured",
      );
      return;
    }
    const detail = kind === "auth"
      ? "OpenAI authentication failed."
      : "OpenAI quota or billing failed.";
    const response = await client.chat.postMessage({
      channel: settings.operationalAlertChannelId,
      text:
        `Hi ${settings.operationalAlertSupportMention}, I'm broken, please check the logs!\n${detail} Treat alerts may be missed until this is fixed. Context: \`${context}\`.`,
    });
    if (!response.ok) {
      console.error(`Failed to send operational alert: ${response.error}`);
    }
  };
}

async function sendTreatAlert(
  client: SlackAPIClient,
  settings: Settings,
  channelId: string,
  ts: string,
  certainty: number | undefined,
) {
  const permalink = await client.chat.getPermalink({
    channel: channelId,
    message_ts: ts,
  });
  const messageUrl = permalink.ok
    ? permalink.permalink
    : `https://slack.com/archives/${channelId}/p${ts.replace(".", "")}`;
  const certaintyInfo = certainty === undefined
    ? "caught on second look"
    : `${certainty}% certainty`;
  const response = await client.chat.postMessage({
    channel: settings.alertChannelId,
    text: `:cake-radar: *<${messageUrl}|Cake detected!>* (${certaintyInfo})`,
  });
  if (!response.ok) {
    console.error(
      `Error sending alert to ${settings.alertChannelId}: ${response.error}`,
    );
  }
}

export type DetectTreatInputs = {
  channel_id: string;
  channel_type: string;
  message_ts: string;
  text?: string;
  user_id?: string;
};

export type DetectTreatDeps = {
  client: SlackAPIClient;
  token: string;
  settings: Settings;
  chat: ChatCompletion;
};

export async function detectTreat(
  inputs: DetectTreatInputs,
  { client, token, settings, chat }: DetectTreatDeps,
): Promise<string> {
  const { channel_id: channelId, message_ts: ts } = inputs;
  const text = inputs.text ?? "";

  if (settings.ignoredChannelIds.has(channelId)) return "skipped_channel";
  if (inputs.channel_type !== "public") return "skipped_private_source";

  const matchedKeywords = findCakeWords(text);
  if (matchedKeywords.length === 0) return "no_keywords";

  const message = await fetchTopLevelMessage(client, channelId, ts);
  if (message && message.text !== text) return "skipped_thread_reply";

  if (!await claimMessage(client, `${channelId}:${ts}`)) return "duplicate";

  const alert = operationalAlerter(client, settings);
  const images = await downloadFirstImage(message?.files ?? [], token);
  const assessment = await assessCertainty(
    chat,
    settings,
    text.toLowerCase(),
    images,
    alert,
  );

  const classifierYes = isClassifierYes(
    assessment,
    settings.certaintyThreshold,
  );
  const panel = classifierYes
    ? await judgePanel(chat, settings, text, assessment.reason, images, alert)
    : undefined;
  const rescue = !classifierYes && assessment.decision !== "error"
    ? await rescueJudge(chat, settings, text, assessment.reason, images, alert)
    : undefined;
  const rescued = rescue?.verdict === "rescue";
  const forwarded = (classifierYes && panel?.verdict !== "overturn") ||
    rescued;

  console.log(
    [
      "EVALUATED",
      forwarded ? "FORWARDED" : "NOT_FORWARDED",
      `AI=${assessment.decision} ${assessment.certainty}%`,
      assessment.reason && `reason=${assessment.reason}`,
      panel && `judge_panel=${panel.verdict}`,
      panel && `judge_votes=[${formatVotes(panel.votes)}]`,
      rescue && `rescue_judge=${rescue.verdict}`,
      rescue && `judge_reason=${rescue.reason}`,
      `keywords=${JSON.stringify(matchedKeywords)}`,
      `images=${images.length}`,
      channelId,
    ].filter(Boolean).join(" | "),
  );

  if (!forwarded) return "not_forwarded";
  await sendTreatAlert(
    client,
    settings,
    channelId,
    ts,
    rescued ? undefined : assessment.certainty,
  );
  return rescued ? "rescued" : "forwarded";
}

export default SlackFunction(
  DetectTreatFunctionDefinition,
  async ({ inputs, client, env, token }) => {
    const settings = loadSettings(env);
    const outcome = await detectTreat(inputs, {
      client,
      token,
      settings,
      chat: openAIChatCompletion(settings.openaiApiKey),
    });
    console.log(`OUTCOME | ${outcome} | ${inputs.channel_id}`);
    return { outputs: { outcome } };
  },
);
