Cake Radar is a lightweight Slack bot that helps colleagues find shared treats before they disappear.

- Watches Slack for messages about cake, snacks, drinks, and other office treats
- Posts likely treat sightings to a dedicated alert channel
- Uses a small judge panel so one overly strict check does not block a real treat

## How It Works

Cake Radar first looks for messages that mention treat-related words. When a message looks promising, it asks an AI classifier whether this is likely about food or drink that colleagues can actually get.

If the classifier is confident, four judges review the candidate from different angles:

- Is the treat available now or very soon?
- Is this clearly a false alarm, like a metaphor, future event, private lunch, or non-food item?
- Does the message read like someone is alerting colleagues to shared office food?
- Would a hungry colleague reasonably want to know about this sighting?

Cake Radar only suppresses an alert when at least three judges agree it is a false alarm. Informal sightings still count, so "cake at the entrance" should be enough.

Logs include the classifier result, the final judge-panel outcome, and each judge's vote with its reason.

## Summoning Cake Radar

Cake Radar only sees channels it has been added to. If it missed a treat, tag `@Cake Radar` in the thread, or in the channel right after the post.

- If Slack asks whether to invite Cake Radar, click **Invite**. It picks up tags from the last hour as soon as it joins.
- It scans the whole thread, or the last 10 top-level messages from today. The tag is a strong hint, so the bar is lower than for live alerts and the judge panel is skipped.
- It reacts :cake-radar: when the treat is on the alert channel (new or already posted), and :x: when it finds no treat.
- Adding Cake Radar to a channel without tagging it never posts old messages. Private channels are never cross-posted.

## Code map

One Slack message follows this route:

`app.py` receives it → `message_processor.py` applies the safety checks, cake-word check, duplicate-alert protection, image handling and final alert → `ai_classifier.py` asks OpenAI and the judge panel for an opinion.

- `app.py`: starts the web app and connects Slack events to Cake Radar.
- `message_processor.py`: the complete decision path for a Slack message.
- `summon.py`: handles tags of Cake Radar and the catch-up after it is invited to a channel.
- `ai_classifier.py`: OpenAI classifier and judge-panel calls.
- `config.py`: environment settings and prompts.
- `keywords.json`: the cake and snack words Cake Radar recognises.
