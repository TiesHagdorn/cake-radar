import type { Trigger } from "deno-slack-sdk/types.ts";
import {
  TriggerContextData,
  TriggerEventTypes,
  TriggerTypes,
} from "deno-slack-api/mod.ts";
import DetectTreatWorkflow from "../workflows/detect_treat_workflow.ts";

const data = TriggerContextData.Event.MessagePosted;

const messagePostedTrigger: Trigger<typeof DetectTreatWorkflow.definition> = {
  type: TriggerTypes.Event,
  name: "Cake Radar: new message",
  description: "Check new top-level human messages for treat offerings",
  workflow: `#/workflows/${DetectTreatWorkflow.definition.callback_id}`,
  event: {
    event_type: TriggerEventTypes.MessagePosted,
    all_resources: true,
    filter: {
      version: 1,
      root: {
        operator: "AND",
        inputs: [
          { statement: "{{data.user_id}} != null" },
          { statement: "{{data.thread_ts}} == null" },
          { statement: "{{data.app_id}} == null" },
        ],
      },
    },
  },
  inputs: {
    channel_id: { value: data.channel_id },
    channel_type: { value: data.channel_type },
    message_ts: { value: data.message_ts },
    text: { value: data.text },
    user_id: { value: data.user_id },
  },
};

export default messagePostedTrigger;
