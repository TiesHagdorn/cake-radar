"""Ask OpenAI whether a Slack message is an alert-worthy shared treat."""

import json
import logging
from typing import Callable, Dict, List

from .config import Config


def openai_operational_error_kind(error: Exception) -> str:
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
    return [{"type": "text", "text": prompt_text}] + [
        {"type": "image_url", "image_url": {"url": uri, "detail": "low"}}
        for uri in image_data_uris
    ]


def _usage(response):
    usage = getattr(response, 'usage', None)
    return {'prompt_tokens': getattr(usage, 'prompt_tokens', 0), 'completion_tokens': getattr(usage, 'completion_tokens', 0)}


def parse_classifier_response(raw_response: str, response=None) -> Dict:
    try:
        parsed = json.loads(raw_response)
        decision = str(parsed.get('decision', '')).strip().lower()
        if decision not in ('yes', 'no'):
            raise ValueError(f"unexpected decision {decision!r}")
        return {
            'decision': decision,
            'total_certainty': max(0, min(int(parsed.get('certainty', parsed.get('total_certainty', 0))), 100)),
            'reason': str(parsed.get('reason', '')).strip().lower(),
            **_usage(response),
        }
    except Exception as error:
        logging.error(f"Error parsing OpenAI response: {error}")
        return {'decision': 'no', 'total_certainty': 0, 'reason': '', 'prompt_tokens': 0, 'completion_tokens': 0}


def assess_certainty(openai_client, message_text: str, notify_operational_error: Callable[[Exception, str], None], image_data_uris: List[str] = None) -> Dict:
    prompt_text = Config.USER_PROMPT_TEMPLATE.format(message_text=message_text)
    user_content = _user_content(prompt_text, image_data_uris)

    def call_openai(content):
        return openai_client.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[{"role": "system", "content": Config.SYSTEM_PROMPT}, {"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
    try:
        response = call_openai(user_content)
    except Exception as error:
        notify_operational_error(error, 'classifier')
        if openai_operational_error_kind(error):
            logging.error(f"OpenAI classifier operational error: {error}")
            return {'decision': 'error', 'total_certainty': 0, 'reason': openai_operational_error_kind(error), 'prompt_tokens': 0, 'completion_tokens': 0}
        if not image_data_uris:
            logging.error(f"Error assessing certainty: {error}")
            return {'decision': 'no', 'total_certainty': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
        logging.warning(f"OpenAI image error, retrying without images: {error}")
        try:
            response = call_openai(prompt_text)
        except Exception as retry_error:
            notify_operational_error(retry_error, 'classifier_retry_without_images')
            logging.error(f"Error assessing certainty: {retry_error}")
            return {'decision': 'error' if openai_operational_error_kind(retry_error) else 'no', 'total_certainty': 0, 'reason': openai_operational_error_kind(retry_error), 'prompt_tokens': 0, 'completion_tokens': 0}
    return parse_classifier_response(response.choices[0].message.content, response)


def parse_judge_response(raw_response: str) -> Dict:
    try:
        parsed = json.loads(raw_response)
        verdict = str(parsed.get('verdict', '')).strip().lower()
        if verdict not in ('uphold', 'overturn'):
            logging.warning(f"Judge returned unexpected verdict {verdict!r}, defaulting to uphold")
            return {'verdict': 'uphold', 'reason': f'parse_error: {raw_response[:80]}'}
        return {'verdict': verdict, 'reason': str(parsed.get('reason', '')).strip().lower()}
    except Exception as error:
        logging.error(f"Error parsing judge response, defaulting to uphold: {error}")
        return {'verdict': 'uphold', 'reason': 'parse_error'}


def _run_judge(openai_client, judge_config: Dict, prompt_text: str, user_content, notify_operational_error) -> Dict:
    judge_name = judge_config['name']
    def call_openai(content):
        return openai_client.chat.completions.create(model=Config.JUDGE_MODEL, messages=[{"role": "system", "content": judge_config['prompt']}, {"role": "user", "content": content}], response_format={"type": "json_object"})
    try:
        response = call_openai(user_content)
    except Exception as error:
        notify_operational_error(error, f'judge_{judge_name}')
        if user_content == prompt_text:
            logging.error(f"Judge {judge_name} error, defaulting to uphold: {error}")
            return {'name': judge_name, 'verdict': 'uphold', 'reason': 'judge_error'}
        logging.warning(f"Judge {judge_name} image error, retrying without images: {error}")
        try:
            response = call_openai(prompt_text)
        except Exception as retry_error:
            notify_operational_error(retry_error, f'judge_{judge_name}_retry_without_images')
            logging.error(f"Judge {judge_name} error, defaulting to uphold: {retry_error}")
            return {'name': judge_name, 'verdict': 'uphold', 'reason': 'judge_error'}
    try:
        return {'name': judge_name, **parse_judge_response(response.choices[0].message.content)}
    except Exception as error:
        logging.error(f"Error parsing judge {judge_name} response, defaulting to uphold: {error}")
        return {'name': judge_name, 'verdict': 'uphold', 'reason': 'parse_error'}


def judge_decision(openai_client, message_text: str, classifier_reason: str, notify_operational_error: Callable[[Exception, str], None], image_data_uris: List[str] = None) -> Dict:
    prompt_text = Config.JUDGE_USER_PROMPT_TEMPLATE.format(message_text=message_text, classifier_reason=classifier_reason)
    user_content = _user_content(prompt_text, image_data_uris)
    votes = [_run_judge(openai_client, judge, prompt_text, user_content, notify_operational_error) for judge in Config.JUDGE_SYSTEM_PROMPTS]
    verdict = 'overturn' if sum(vote['verdict'] == 'overturn' for vote in votes) >= 3 else 'uphold'
    return {'verdict': verdict, 'reason': '; '.join(f"{vote['name']}={vote['verdict']}:{vote['reason']}" for vote in votes), 'votes': votes}


def format_judge_votes(votes: List[Dict]) -> str:
    return '; '.join(f"{vote.get('name', 'unknown')}={vote.get('verdict', 'unknown')} ({vote.get('reason', '')})" for vote in votes)
