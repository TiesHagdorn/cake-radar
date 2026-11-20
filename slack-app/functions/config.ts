import baseKeywords from "../../cake_radar/keywords.json" with {
  type: "json",
};

export type Env = Record<string, string>;

export const DEFAULTS = {
  ALERT_CHANNEL_ID: "C07SBFP9GDR",
  OPERATIONAL_ALERT_CHANNEL_ID: "C07SBFP9GDR",
  OPERATIONAL_ALERT_SUPPORT_MENTION: "",
  IGNORED_CHANNEL_IDS: "C07RTPCLAKC",
  OPENAI_MODEL: "gpt-6-luna",
  JUDGE_MODEL: "gpt-6.1-sol",
  CERTAINTY_THRESHOLD: "85",
};

export type Settings = {
  openaiApiKey: string;
  alertChannelId: string;
  operationalAlertChannelId: string;
  operationalAlertSupportMention: string;
  ignoredChannelIds: Set<string>;
  openaiModel: string;
  judgeModel: string;
  certaintyThreshold: number;
};

export function loadSettings(env: Env): Settings {
  const get = (key: keyof typeof DEFAULTS) => env[key] ?? DEFAULTS[key];
  const alertChannelId = get("ALERT_CHANNEL_ID");
  const ignored = get("IGNORED_CHANNEL_IDS").split(",").map((id) => id.trim())
    .filter(Boolean);
  return {
    openaiApiKey: env.OPENAI_API_KEY ?? "",
    alertChannelId,
    operationalAlertChannelId: get("OPERATIONAL_ALERT_CHANNEL_ID"),
    operationalAlertSupportMention: get("OPERATIONAL_ALERT_SUPPORT_MENTION"),
    ignoredChannelIds: new Set([alertChannelId, ...ignored]),
    openaiModel: get("OPENAI_MODEL"),
    judgeModel: get("JUDGE_MODEL"),
    certaintyThreshold: Number(get("CERTAINTY_THRESHOLD")),
  };
}

export const KEYWORDS: string[] = [
  ...baseKeywords,
  ...baseKeywords.map((keyword: string) => `${keyword}s`),
];
