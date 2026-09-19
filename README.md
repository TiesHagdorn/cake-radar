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

## Code map

One Slack message follows this route:

`app.py` receives it → `message_processor.py` applies the safety checks, cake-word check, duplicate-alert protection, image handling and final alert → `ai_classifier.py` asks OpenAI and the judge panel for an opinion.

- `app.py`: starts the web app and connects Slack events to Cake Radar.
- `message_processor.py`: the complete decision path for a Slack message.
- `ai_classifier.py`: OpenAI classifier and judge-panel calls.
- `config.py`: environment settings and prompts.
- `keywords.json`: the cake and snack words Cake Radar recognises.
