Cake Radar is a lightweight Slack bot that helps colleagues find shared treats before they disappear.

- Watches Slack for messages about cake, snacks, drinks, and other office treats
- Posts likely treat sightings to a dedicated alert channel
- Uses a small judge panel so one overly strict check does not block a real treat

## How It Works

Cake Radar first looks for messages that mention treat-related words. When a message looks promising, it asks an AI classifier whether this is likely about food or drink that colleagues can actually get.

If the classifier is confident, three judges review the candidate from different angles:

- Is the treat available now or very soon?
- Is this clearly a false alarm, like a metaphor, future event, private lunch, or non-food item?
- Does the message read like someone is alerting colleagues to shared office food?

Cake Radar only suppresses an alert when at least two judges agree it is a false alarm. Informal sightings still count, so "cake at the entrance" should be enough.

Logs include the classifier result, the final judge-panel outcome, and each judge's vote with its reason.
