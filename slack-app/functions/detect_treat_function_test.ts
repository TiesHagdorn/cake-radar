import { assertEquals } from "@std/assert";
import type { SlackAPIClient } from "deno-slack-sdk/types.ts";
import type { ChatCompletion } from "./classifier.ts";
import { loadSettings } from "./config.ts";
import {
  JUDGE_SYSTEM_PROMPTS,
  RESCUE_JUDGE,
  SYSTEM_PROMPT,
} from "./prompts.ts";
import { detectTreat } from "./detect_treat_function.ts";

type Posted = { channel: string; text: string };

function fakeClient(
  posted: Posted[],
  stored = new Set<string>(),
  history: { ts: string; text: string }[] = [],
) {
  return {
    apps: {
      datastore: {
        get: ({ id }: { id: string }) =>
          Promise.resolve({ ok: true, item: stored.has(id) ? { id } : {} }),
        put: ({ item }: { item: { id: string } }) => {
          stored.add(item.id);
          return Promise.resolve({ ok: true });
        },
      },
    },
    conversations: {
      history: () => Promise.resolve({ ok: true, messages: history }),
    },
    chat: {
      getPermalink: () =>
        Promise.resolve({ ok: true, permalink: "https://example/p1" }),
      postMessage: (args: Posted) => {
        posted.push(args);
        return Promise.resolve({ ok: true });
      },
    },
  } as unknown as SlackAPIClient;
}

type Replies = {
  classifier: object;
  panel?: string[];
  rescue?: string;
};

const fakeChat = ({ classifier, panel = [], rescue = "confirm" }: Replies) => {
  const calls: string[] = [];
  const chat: ChatCompletion = (_model, systemPrompt) => {
    const judgeIndex = JUDGE_SYSTEM_PROMPTS.findIndex((judge) =>
      judge.prompt === systemPrompt
    );
    if (systemPrompt === SYSTEM_PROMPT) {
      calls.push("classifier");
      return Promise.resolve(JSON.stringify(classifier));
    }
    if (systemPrompt === RESCUE_JUDGE.prompt) {
      calls.push("rescue");
      return Promise.resolve(JSON.stringify({ verdict: rescue, reason: "r" }));
    }
    calls.push(`judge_${judgeIndex}`);
    return Promise.resolve(
      JSON.stringify({ verdict: panel[judgeIndex] ?? "uphold", reason: "p" }),
    );
  };
  return { chat, calls };
};

const settings = loadSettings({ OPENAI_API_KEY: "test" });
const message = {
  channel_id: "C123",
  channel_type: "public",
  message_ts: "1700000000.000100",
  text: "I brought cake to the office",
};
const yes95 = { decision: "yes", certainty: 95, reason: "cake offered" };
const no80 = { decision: "no", certainty: 80, reason: "future event" };

const run = async (replies: Replies, inputs = message) => {
  const posted: Posted[] = [];
  const { chat, calls } = fakeChat(replies);
  const outcome = await detectTreat(inputs, {
    client: fakeClient(posted),
    token: "xoxb-test",
    settings,
    chat,
  });
  return { outcome, posted, calls };
};

Deno.test("forwards a classifier yes the judge panel upholds", async () => {
  const { outcome, posted, calls } = await run({ classifier: yes95 });
  assertEquals(outcome, "forwarded");
  assertEquals(calls.filter((call) => call.startsWith("judge_")).length, 4);
  assertEquals(posted, [{
    channel: "C07SBFP9GDR",
    text: ":cake-radar: *<https://example/p1|Cake detected!>* (95% certainty)",
  }]);
});

Deno.test("still forwards when only two judges overturn", async () => {
  const { outcome } = await run({
    classifier: yes95,
    panel: ["overturn", "overturn", "uphold", "uphold"],
  });
  assertEquals(outcome, "forwarded");
});

Deno.test("suppresses the alert when three judges overturn", async () => {
  const { outcome, posted } = await run({
    classifier: yes95,
    panel: ["overturn", "overturn", "overturn", "uphold"],
  });
  assertEquals(outcome, "not_forwarded");
  assertEquals(posted, []);
});

Deno.test("rescue judge can still send a rejected treat", async () => {
  const { outcome, posted, calls } = await run({
    classifier: no80,
    rescue: "rescue",
  });
  assertEquals(outcome, "rescued");
  assertEquals(calls, ["classifier", "rescue"]);
  assertEquals(
    posted[0].text,
    ":cake-radar: *<https://example/p1|Cake detected!>* (caught on second look)",
  );
});

Deno.test("a confirmed rejection sends nothing", async () => {
  const { outcome, posted } = await run({ classifier: no80 });
  assertEquals(outcome, "not_forwarded");
  assertEquals(posted, []);
});

Deno.test("skips without calling OpenAI when no keyword matches", async () => {
  const { outcome, calls } = await run({ classifier: yes95 }, {
    ...message,
    text: "standup moved to 10:00",
  });
  assertEquals(outcome, "no_keywords");
  assertEquals(calls, []);
});

Deno.test("never evaluates private channels, DMs or group DMs", async () => {
  for (const channel_type of ["private", "im", "mpdm"]) {
    const { outcome, calls } = await run({ classifier: yes95 }, {
      ...message,
      channel_type,
    });
    assertEquals(outcome, "skipped_private_source");
    assertEquals(calls, []);
  }
});

Deno.test("skips ignored channels and duplicates", async () => {
  const deps = {
    client: fakeClient([], new Set(["C123:1700000000.000100"])),
    token: "xoxb-test",
    settings,
    chat: fakeChat({ classifier: yes95 }).chat,
  };
  assertEquals(
    await detectTreat({ ...message, channel_id: "C07SBFP9GDR" }, deps),
    "skipped_channel",
  );
  assertEquals(
    await detectTreat({ ...message, channel_id: "C07RTPCLAKC" }, deps),
    "skipped_channel",
  );
  assertEquals(await detectTreat(message, deps), "duplicate");
});

Deno.test("skips thread replies, which carry the parent message ts", async () => {
  const { chat, calls } = fakeChat({ classifier: yes95 });
  const parent = { ts: message.message_ts, text: "Team update for Q3" };
  const outcome = await detectTreat(message, {
    client: fakeClient([], new Set(), [parent]),
    token: "xoxb-test",
    settings,
    chat,
  });
  assertEquals(outcome, "skipped_thread_reply");
  assertEquals(calls, []);
});

Deno.test("posts an operational alert on OpenAI auth failure", async () => {
  const posted: Posted[] = [];
  const chat: ChatCompletion = () =>
    Promise.reject(new Error("invalid_api_key"));
  const outcome = await detectTreat(message, {
    client: fakeClient(posted),
    token: "xoxb-test",
    settings,
    chat,
  });
  assertEquals(outcome, "not_forwarded");
  assertEquals(posted.length, 1);
  assertEquals(posted[0].text.includes("OpenAI authentication failed."), true);
});
