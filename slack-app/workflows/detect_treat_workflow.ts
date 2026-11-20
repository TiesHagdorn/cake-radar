import { DefineWorkflow, Schema } from "deno-slack-sdk/mod.ts";
import { DetectTreatFunctionDefinition } from "../functions/detect_treat_function.ts";

const DetectTreatWorkflow = DefineWorkflow({
  callback_id: "detect_treat_workflow",
  title: "Detect treat",
  description: "Check a new message for treat offerings",
  input_parameters: {
    properties: {
      channel_id: { type: Schema.slack.types.channel_id },
      channel_type: { type: Schema.types.string },
      message_ts: { type: Schema.types.string },
      text: { type: Schema.types.string },
      user_id: { type: Schema.slack.types.user_id },
    },
    required: ["channel_id", "channel_type", "message_ts"],
  },
});

DetectTreatWorkflow.addStep(DetectTreatFunctionDefinition, {
  channel_id: DetectTreatWorkflow.inputs.channel_id,
  channel_type: DetectTreatWorkflow.inputs.channel_type,
  message_ts: DetectTreatWorkflow.inputs.message_ts,
  text: DetectTreatWorkflow.inputs.text,
  user_id: DetectTreatWorkflow.inputs.user_id,
});

export default DetectTreatWorkflow;
