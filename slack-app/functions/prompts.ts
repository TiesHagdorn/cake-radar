export type JudgeConfig = { name: string; prompt: string };

export const SYSTEM_PROMPT =
  "You are a helpful assistant that evaluates whether a Slack message is about offering an edible treat that is currently available or being offered imminently (e.g. 'I brought cake', 'there are snacks in the kitchen'). Do NOT classify as yes if the message is about a future event, party invitation, or calendar announcement, even if food will be present. You may receive a text message, an image, or both. Respond only with a JSON object containing: decision ('yes' or 'no'), certainty (0-100), and reason (brief string).";

const USER_PROMPT_TEMPLATE =
  "Respond only as JSON with keys decision, certainty, and reason. The offered item MUST be edible food or a drink — non-food items such as books, merchandise, swag, stickers, or any physical item that cannot be eaten do not qualify, even if they are free or described as a treat. If the message mentions a location or hub outside of Amsterdam, be more confident in 'no'. If the message is primarily about work topics and only tangentially mentions food (e.g. a meeting agenda that includes lunch), be more confident in 'no'. However, if the message clearly offers or announces available treats — even alongside work context like a milestone celebration — classify based on the treat offering. If the message is directed at someone else (e.g. wishing them happy birthday, congratulating them), it is not a treat offering — be very confident in 'no'. Only say 'yes' when the author themselves is offering or announcing available food. If an image is attached and it clearly shows an edible treat, increase your confidence in 'yes'. Message: '{message_text}'";

export const JUDGE_SYSTEM_PROMPTS: JudgeConfig[] = [{
  "name": "availability",
  "prompt":
    "Cake Radar alerts colleagues when edible food or drink is likely available to share in the Amsterdam office. The classifier already found this message likely relevant. Your role is only to decide whether there is a clear reason to veto the alert. Focus on availability. Uphold if the message suggests edible food or drink is available now or imminently in a shared Amsterdam office location. Overturn only if it is clearly future, already gone, outside Amsterdam, or private/personal.",
}, {
  "name": "false_positive",
  "prompt":
    "Cake Radar alerts colleagues when edible food or drink is likely available to share in the Amsterdam office. The classifier already found this message likely relevant. Your role is only to decide whether there is a clear reason to veto the alert. Focus on known false positives. Overturn only if the message is clearly about a non-food item, an idiom/metaphor, a future event, an out-of-scope location, private/personal food, or a birthday/congrats message with no available food. Otherwise uphold.",
}, {
  "name": "social_context",
  "prompt":
    "Cake Radar alerts colleagues when edible food or drink is likely available to share in the Amsterdam office. The classifier already found this message likely relevant. Your role is only to decide whether there is a clear reason to veto the alert. Focus on social intent. Uphold if the message reads like someone is alerting colleagues to shared office food, including informal sightings. Overturn only if it reads merely as a joke, personal wish, work agenda mention, or private meal.",
}, {
  "name": "hungry",
  "prompt":
    "Cake Radar alerts colleagues when edible food or drink is likely available to share in the Amsterdam office. The classifier already found this message likely relevant. Your role is only to decide whether there is a clear reason to veto the alert. Focus on appetite and recall. Uphold if a hungry colleague would reasonably want to know about this message, including terse sightings like 'cake at entrance'. Overturn only if it is clearly a non-food item, a future event, an out-of-scope location, already gone, private/personal food, or an idiom/metaphor.",
}];

const JUDGE_USER_PROMPT_TEMPLATE =
  "The classifier said YES with reason: '{classifier_reason}'.\n\nMessage: '{message_text}'\n\nRespond only as JSON with keys verdict and reason. Verdict must be 'uphold' or 'overturn'.";

export const RESCUE_JUDGE: JudgeConfig = {
  "name": "rescue",
  "prompt":
    "Cake Radar alerts colleagues when edible food or drink is likely available to share in the Amsterdam office. The classifier did NOT find this message alert-worthy. Your role is only to catch treats it missed. Rescue if the author announces edible food or drink that is available to share now or today, even when that announcement is part of a longer work update or celebration (e.g. 'to celebrate, there's cake at your work location today, go grab a slice'). Otherwise confirm, and keep confirming future events, invitations, birthday or congrats messages aimed at someone else, non-food items, idioms, and private food.",
};

const RESCUE_JUDGE_USER_PROMPT_TEMPLATE =
  "The classifier said NO with reason: '{classifier_reason}'.\n\nMessage: '{message_text}'\n\nRespond only as JSON with keys verdict and reason. Verdict must be 'rescue' or 'confirm'.";

const fill = (template: string, values: Record<string, string>) =>
  template.replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);

export const userPrompt = (messageText: string) =>
  fill(USER_PROMPT_TEMPLATE, { message_text: messageText });

export const judgeUserPrompt = (
  messageText: string,
  classifierReason: string,
) =>
  fill(JUDGE_USER_PROMPT_TEMPLATE, {
    message_text: messageText,
    classifier_reason: classifierReason,
  });

export const rescueUserPrompt = (
  messageText: string,
  classifierReason: string,
) =>
  fill(RESCUE_JUDGE_USER_PROMPT_TEMPLATE, {
    message_text: messageText,
    classifier_reason: classifierReason,
  });
