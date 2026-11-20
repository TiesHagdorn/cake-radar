import { DefineDatastore, Schema } from "deno-slack-sdk/mod.ts";

export const ProcessedMessagesDatastore = DefineDatastore({
  name: "processed_messages",
  primary_key: "id",
  attributes: {
    id: { type: Schema.types.string },
    expires_at: { type: Schema.slack.types.timestamp },
  },
  time_to_live_attribute: "expires_at",
});
