import { Manifest } from "deno-slack-sdk/mod.ts";
import { ProcessedMessagesDatastore } from "./datastores/processed_messages.ts";
import DetectTreatWorkflow from "./workflows/detect_treat_workflow.ts";

export default Manifest({
  name: "Cake Radar",
  description: "Spots treat offerings in Slack and posts an alert",
  icon: "assets/icon.png",
  workflows: [DetectTreatWorkflow],
  datastores: [ProcessedMessagesDatastore],
  outgoingDomains: ["api.openai.com", "files.slack.com"],
  botScopes: [
    "channels:history",
    "chat:write",
    "chat:write.public",
    "files:read",
    "datastore:read",
    "datastore:write",
  ],
});
