import { assertEquals } from "@std/assert";
import {
  findCakeWords,
  isClassifierYes,
  OpenAIError,
  operationalErrorKind,
  parseClassifierResponse,
  parseJudgeResponse,
} from "./classifier.ts";

Deno.test("findCakeWords matches standalone words and plurals", () => {
  assertEquals(
    findCakeWords("I brought cake to the office").includes("cake"),
    true,
  );
  assertEquals(
    findCakeWords("Cookies in the kitchen").includes("cookies"),
    true,
  );
  assertEquals(findCakeWords("pancakes are great").includes("cake"), false);
  assertEquals(findCakeWords("quarterly planning sync"), []);
});

Deno.test("parseClassifierResponse reads JSON and clamps certainty", () => {
  assertEquals(
    parseClassifierResponse(
      '{"decision":"Yes","certainty":120,"reason":"Cake on desk"}',
    ),
    { decision: "yes", certainty: 100, reason: "cake on desk" },
  );
  assertEquals(parseClassifierResponse("Yes, 95%, cake"), {
    decision: "no",
    certainty: 0,
    reason: "",
  });
  assertEquals(
    parseClassifierResponse('{"decision":"maybe","certainty":90}').decision,
    "no",
  );
});

Deno.test("isClassifierYes requires yes above threshold", () => {
  assertEquals(
    isClassifierYes({ decision: "yes", certainty: 90, reason: "" }, 85),
    true,
  );
  assertEquals(
    isClassifierYes({ decision: "yes", certainty: 85, reason: "" }, 85),
    false,
  );
});

Deno.test("parseJudgeResponse falls back to the safe verdict", () => {
  const panel = ["uphold", "overturn"] as const;
  const rescue = ["confirm", "rescue"] as const;
  assertEquals(
    parseJudgeResponse('{"verdict":"Overturn","reason":"Not food"}', panel),
    { verdict: "overturn", reason: "not food" },
  );
  assertEquals(
    parseJudgeResponse('{"verdict":"maybe"}', panel).verdict,
    "uphold",
  );
  assertEquals(parseJudgeResponse("not json", rescue).verdict, "confirm");
});

Deno.test("operationalErrorKind detects auth and quota failures", () => {
  assertEquals(operationalErrorKind(new OpenAIError(401, "{}")), "auth");
  assertEquals(
    operationalErrorKind(
      new OpenAIError(429, '{"error":{"code":"insufficient_quota"}}'),
    ),
    "quota",
  );
  assertEquals(operationalErrorKind(new OpenAIError(500, "oops")), "");
});
