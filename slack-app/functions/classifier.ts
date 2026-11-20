import { KEYWORDS, type Settings } from "./config.ts";
import {
  JUDGE_SYSTEM_PROMPTS,
  type JudgeConfig,
  judgeUserPrompt,
  RESCUE_JUDGE,
  rescueUserPrompt,
  SYSTEM_PROMPT,
  userPrompt,
} from "./prompts.ts";

export type Assessment = {
  decision: "yes" | "no" | "error";
  certainty: number;
  reason: string;
};

export type JudgeVote = { name: string; verdict: string; reason: string };

export type PanelResult = {
  verdict: "uphold" | "overturn";
  votes: JudgeVote[];
};

export type OperationalErrorKind = "auth" | "quota" | "";

export class OpenAIError extends Error {
  constructor(readonly status: number, readonly body: string) {
    super(`OpenAI request failed with status ${status}: ${body.slice(0, 300)}`);
  }
}

const escapeRegExp = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const keywordPatterns = KEYWORDS.map((keyword) => ({
  keyword,
  pattern: new RegExp(
    `(?<![\\p{L}\\p{N}_])${escapeRegExp(keyword)}(?![\\p{L}\\p{N}_])`,
    "iu",
  ),
}));

export function findCakeWords(text: string): string[] {
  return keywordPatterns
    .filter(({ pattern }) => pattern.test(text))
    .map(({ keyword }) => keyword);
}

export function parseClassifierResponse(raw: string): Assessment {
  try {
    const parsed = JSON.parse(raw);
    const decision = String(parsed.decision ?? "").trim().toLowerCase();
    if (decision !== "yes" && decision !== "no") {
      throw new Error(`unexpected decision ${JSON.stringify(decision)}`);
    }
    const certainty = Math.trunc(
      Number(parsed.certainty ?? parsed.total_certainty ?? 0),
    );
    if (Number.isNaN(certainty)) throw new Error("certainty is not a number");
    return {
      decision,
      certainty: Math.max(0, Math.min(certainty, 100)),
      reason: String(parsed.reason ?? "").trim().toLowerCase(),
    };
  } catch (error) {
    console.error(`Error parsing OpenAI response: ${error}`);
    return { decision: "no", certainty: 0, reason: "" };
  }
}

export function parseJudgeResponse(
  raw: string,
  verdicts: readonly [string, string],
): { verdict: string; reason: string } {
  const fallback = verdicts[0];
  try {
    const parsed = JSON.parse(raw);
    const verdict = String(parsed.verdict ?? "").trim().toLowerCase();
    if (!verdicts.includes(verdict)) {
      console.warn(
        `Judge returned unexpected verdict ${
          JSON.stringify(verdict)
        }, defaulting to ${fallback}`,
      );
      return { verdict: fallback, reason: `parse_error: ${raw.slice(0, 80)}` };
    }
    return {
      verdict,
      reason: String(parsed.reason ?? "").trim().toLowerCase(),
    };
  } catch (error) {
    console.error(
      `Error parsing judge response, defaulting to ${fallback}: ${error}`,
    );
    return { verdict: fallback, reason: "parse_error" };
  }
}

export function operationalErrorKind(error: unknown): OperationalErrorKind {
  const status = error instanceof OpenAIError ? error.status : undefined;
  const text = String(error instanceof OpenAIError ? error.body : error)
    .toLowerCase();
  if (
    status === 401 || status === 403 || text.includes("invalid_api_key") ||
    text.includes("incorrect api key")
  ) {
    return "auth";
  }
  if (text.includes("insufficient_quota") || text.includes("billing")) {
    return "quota";
  }
  return "";
}

export function isClassifierYes(assessment: Assessment, threshold: number) {
  return assessment.decision === "yes" && assessment.certainty > threshold;
}

type ChatContent =
  | string
  | Array<
    | { type: "text"; text: string }
    | { type: "image_url"; image_url: { url: string; detail: "low" } }
  >;

export type ChatCompletion = (
  model: string,
  systemPrompt: string,
  content: ChatContent,
) => Promise<string>;

const OPENAI_TIMEOUT_MS = 20_000;

export function openAIChatCompletion(
  apiKey: string,
  fetchImpl: typeof fetch = fetch,
): ChatCompletion {
  return async (model, systemPrompt, content) => {
    const response = await fetchImpl(
      "https://api.openai.com/v1/chat/completions",
      {
        method: "POST",
        signal: AbortSignal.timeout(OPENAI_TIMEOUT_MS),
        headers: {
          "Authorization": `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          messages: [
            { role: "system", content: systemPrompt },
            { role: "user", content },
          ],
          response_format: { type: "json_object" },
        }),
      },
    );
    const body = await response.text();
    if (!response.ok) throw new OpenAIError(response.status, body);
    return JSON.parse(body).choices[0].message.content;
  };
}

const withImages = (text: string, imageDataUris: string[]): ChatContent =>
  imageDataUris.length === 0 ? text : [
    { type: "text", text },
    ...imageDataUris.map((url) => ({
      type: "image_url" as const,
      image_url: { url, detail: "low" as const },
    })),
  ];

export type OperationalAlert = (
  kind: OperationalErrorKind,
  context: string,
) => Promise<void>;

async function callWithImageFallback(
  call: (content: ChatContent) => Promise<string>,
  text: string,
  imageDataUris: string[],
  context: string,
  alert: OperationalAlert,
  retryOnOperationalError: boolean,
): Promise<string> {
  try {
    return await call(withImages(text, imageDataUris));
  } catch (error) {
    const kind = operationalErrorKind(error);
    if (kind) await alert(kind, context);
    if (imageDataUris.length === 0 || (kind && !retryOnOperationalError)) {
      throw error;
    }
    console.warn(`${context} image error, retrying without images: ${error}`);
    try {
      return await call(text);
    } catch (retryError) {
      const retryKind = operationalErrorKind(retryError);
      if (retryKind) await alert(retryKind, `${context}_retry_without_images`);
      throw retryError;
    }
  }
}

export async function assessCertainty(
  chat: ChatCompletion,
  settings: Settings,
  messageText: string,
  imageDataUris: string[],
  alert: OperationalAlert,
): Promise<Assessment> {
  try {
    const raw = await callWithImageFallback(
      (content) => chat(settings.openaiModel, SYSTEM_PROMPT, content),
      userPrompt(messageText),
      imageDataUris,
      "classifier",
      alert,
      false,
    );
    return parseClassifierResponse(raw);
  } catch (error) {
    const kind = operationalErrorKind(error);
    console.error(`Error assessing certainty: ${error}`);
    return { decision: kind ? "error" : "no", certainty: 0, reason: kind };
  }
}

async function runJudge(
  chat: ChatCompletion,
  settings: Settings,
  judge: JudgeConfig,
  promptText: string,
  imageDataUris: string[],
  alert: OperationalAlert,
  verdicts: readonly [string, string],
): Promise<JudgeVote> {
  try {
    const raw = await callWithImageFallback(
      (content) => chat(settings.judgeModel, judge.prompt, content),
      promptText,
      imageDataUris,
      `judge_${judge.name}`,
      alert,
      true,
    );
    return { name: judge.name, ...parseJudgeResponse(raw, verdicts) };
  } catch (error) {
    console.error(
      `Judge ${judge.name} error, defaulting to ${verdicts[0]}: ${error}`,
    );
    return { name: judge.name, verdict: verdicts[0], reason: "judge_error" };
  }
}

const PANEL_VERDICTS = ["uphold", "overturn"] as const;
const RESCUE_VERDICTS = ["confirm", "rescue"] as const;
const OVERTURN_QUORUM = 3;

export async function judgePanel(
  chat: ChatCompletion,
  settings: Settings,
  messageText: string,
  classifierReason: string,
  imageDataUris: string[],
  alert: OperationalAlert,
): Promise<PanelResult> {
  const promptText = judgeUserPrompt(messageText, classifierReason);
  const votes = await Promise.all(
    JUDGE_SYSTEM_PROMPTS.map((judge) =>
      runJudge(
        chat,
        settings,
        judge,
        promptText,
        imageDataUris,
        alert,
        PANEL_VERDICTS,
      )
    ),
  );
  const overturns = votes.filter((vote) => vote.verdict === "overturn").length;
  return {
    verdict: overturns >= OVERTURN_QUORUM ? "overturn" : "uphold",
    votes,
  };
}

export function rescueJudge(
  chat: ChatCompletion,
  settings: Settings,
  messageText: string,
  classifierReason: string,
  imageDataUris: string[],
  alert: OperationalAlert,
): Promise<JudgeVote> {
  return runJudge(
    chat,
    settings,
    RESCUE_JUDGE,
    rescueUserPrompt(messageText, classifierReason),
    imageDataUris,
    alert,
    RESCUE_VERDICTS,
  );
}

export const formatVotes = (votes: JudgeVote[]) =>
  votes.map((vote) => `${vote.name}=${vote.verdict} (${vote.reason})`).join(
    "; ",
  );
