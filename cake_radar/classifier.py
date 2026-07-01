import logging
from typing import Callable, Dict, List

from .config import Config


def openai_operational_error_kind(error: Exception) -> str:
    """Return an alert-worthy OpenAI error kind, or an empty string."""
    status_code = getattr(error, 'status_code', None)
    response = getattr(error, 'response', None)
    if status_code is None and response is not None:
        status_code = getattr(response, 'status_code', None)

    error_text = str(error).lower()
    if status_code in (401, 403) or 'invalid_api_key' in error_text or 'incorrect api key' in error_text:
        return 'auth'
    if 'insufficient_quota' in error_text or 'billing' in error_text:
        return 'quota'
    return ''


def _user_content(prompt_text: str, image_data_uris: List[str] = None):
    if not image_data_uris:
        return prompt_text

    user_content = [{"type": "text", "text": prompt_text}]
    for uri in image_data_uris:
        user_content.append({"type": "image_url", "image_url": {"url": uri, "detail": "low"}})
    return user_content


def assess_certainty(
    openai_client,
    message_text: str,
    notify_operational_error: Callable[[Exception, str], None],
    image_data_uris: List[str] = None,
) -> Dict:
    """Assess the likelihood of the message being about offering something."""
    prompt_text = Config.USER_PROMPT_TEMPLATE.format(message_text=message_text)
    user_content = _user_content(prompt_text, image_data_uris)

    def _call_openai(content):
        return openai_client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": Config.SYSTEM_PROMPT},
                {"role": "user", "content": content}
            ]
        )

    try:
        response = _call_openai(user_content)
    except Exception as e:
        notify_operational_error(e, 'classifier')
        if openai_operational_error_kind(e):
            logging.error(f"OpenAI classifier operational error: {e}")
            return {
                'decision': 'error',
                'total_certainty': 0,
                'reason': openai_operational_error_kind(e),
                'prompt_tokens': 0,
                'completion_tokens': 0,
            }
        if image_data_uris:
            logging.warning(f"OpenAI image error, retrying without images: {e}")
            try:
                response = _call_openai(prompt_text)
            except Exception as e2:
                notify_operational_error(e2, 'classifier_retry_without_images')
                logging.error(f"Error assessing certainty: {e2}")
                return {
                    'decision': 'error' if openai_operational_error_kind(e2) else 'no',
                    'total_certainty': 0,
                    'reason': openai_operational_error_kind(e2),
                    'prompt_tokens': 0,
                    'completion_tokens': 0,
                }
        else:
            logging.error(f"Error assessing certainty: {e}")
            return {'decision': 'no', 'total_certainty': 0, 'prompt_tokens': 0, 'completion_tokens': 0}

    reason = ''
    try:
        assessment = response.choices[0].message.content.strip().lower()
        parts = [p.strip() for p in assessment.split(',')]
        decision = parts[0]
        total_certainty = 0
        if len(parts) >= 2:
            total_certainty = int(parts[1].replace('%', ''))
        if len(parts) >= 3:
            reason = ', '.join(parts[2:])
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens
    except Exception as e:
        logging.error(f"Error parsing OpenAI response: {e}")
        return {'decision': 'no', 'total_certainty': 0, 'reason': '', 'prompt_tokens': 0, 'completion_tokens': 0}

    return {
        'decision': decision,
        'total_certainty': total_certainty,
        'reason': reason,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
    }


def parse_judge_response(raw_response: str) -> Dict:
    raw = raw_response.strip().lower()
    verdict, _, reason = raw.partition(',')
    verdict = verdict.strip()
    if verdict not in ('uphold', 'overturn'):
        logging.warning(f"Judge returned unexpected verdict {verdict!r}, defaulting to uphold")
        return {'verdict': 'uphold', 'reason': f'parse_error: {raw[:80]}'}
    return {'verdict': verdict, 'reason': reason.strip()}


def _run_judge(openai_client, judge_config: Dict, prompt_text: str, user_content, notify_operational_error) -> Dict:
    judge_name = judge_config['name']

    def _call(content):
        return openai_client.chat.completions.create(
            model=Config.JUDGE_MODEL,
            messages=[
                {"role": "system", "content": judge_config['prompt']},
                {"role": "user", "content": content},
            ],
        )

    try:
        response = _call(user_content)
    except Exception as e:
        notify_operational_error(e, f'judge_{judge_name}')
        if user_content != prompt_text:
            logging.warning(f"Judge {judge_name} image error, retrying without images: {e}")
            try:
                response = _call(prompt_text)
            except Exception as e2:
                notify_operational_error(e2, f'judge_{judge_name}_retry_without_images')
                logging.error(f"Judge {judge_name} error, defaulting to uphold: {e2}")
                return {'name': judge_name, 'verdict': 'uphold', 'reason': 'judge_error'}
        else:
            logging.error(f"Judge {judge_name} error, defaulting to uphold: {e}")
            return {'name': judge_name, 'verdict': 'uphold', 'reason': 'judge_error'}

    try:
        result = parse_judge_response(response.choices[0].message.content)
        return {'name': judge_name, **result}
    except Exception as e:
        logging.error(f"Error parsing judge {judge_name} response, defaulting to uphold: {e}")
        return {'name': judge_name, 'verdict': 'uphold', 'reason': 'parse_error'}


def judge_decision(
    openai_client,
    message_text: str,
    classifier_reason: str,
    notify_operational_error: Callable[[Exception, str], None],
    image_data_uris: List[str] = None,
) -> Dict:
    """Run a small judge panel over a classifier 'yes'."""
    prompt_text = Config.JUDGE_USER_PROMPT_TEMPLATE.format(
        message_text=message_text, classifier_reason=classifier_reason
    )
    user_content = _user_content(prompt_text, image_data_uris)

    votes = [
        _run_judge(openai_client, judge_config, prompt_text, user_content, notify_operational_error)
        for judge_config in Config.JUDGE_SYSTEM_PROMPTS
    ]
    overturns = sum(1 for vote in votes if vote['verdict'] == 'overturn')
    verdict = 'overturn' if overturns >= 2 else 'uphold'
    reason = '; '.join(
        f"{vote['name']}={vote['verdict']}:{vote['reason']}" for vote in votes
    )
    return {'verdict': verdict, 'reason': reason, 'votes': votes}


def format_judge_votes(votes: List[Dict]) -> str:
    return '; '.join(
        f"{vote.get('name', 'unknown')}={vote.get('verdict', 'unknown')} ({vote.get('reason', '')})"
        for vote in votes
    )

