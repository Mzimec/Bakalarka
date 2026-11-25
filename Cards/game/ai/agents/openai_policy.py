"""OpenAI Responses adapter for the same visible-state/legal-candidate selector.

Uses only the Python standard library. Keys are read at request time from an
environment variable and never included in game configuration or diagnostics.
No retries, conversation history, tools, or rule execution are delegated.
"""
from collections import Counter
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from game.ai.agents.llm_policy import OllamaSelector


class OpenAISelector(OllamaSelector):
    """Share prompt, candidate validation, budgets and fallback with Ollama."""

    def __init__(self, model, *, endpoint='https://api.openai.com/v1/responses',
                 timeout=120, max_requests=100, seed=1, transport=None,
                 include_priors=False, api_key_env='OPENAI_API_KEY',
                 max_output_tokens=2048, reasoning_effort='low'):
        if type(timeout) not in (int, float) or not 0 < timeout <= 600:
            raise ValueError('OpenAI timeout must be in (0, 600] seconds')
        if endpoint != 'https://api.openai.com/v1/responses':
            raise ValueError('OpenAI provider requires https://api.openai.com/v1/responses')
        if not isinstance(api_key_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', api_key_env):
            raise ValueError('api_key_env must name an environment variable')
        if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= 128000:
            raise ValueError('max_output_tokens must be in [16, 128000]')
        if reasoning_effort not in (None, 'none', 'low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Unsupported reasoning_effort')
        super().__init__(model, endpoint=endpoint, timeout=min(timeout, 60),
                         max_requests=max_requests, seed=seed, transport=transport,
                         include_priors=include_priors)
        self.timeout = timeout
        self.api_key_env = api_key_env
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.usage = Counter()

    def _payload(self, observation, candidates):
        # Identical actor-visible prompt; only the transport contract differs.
        messages = super()._payload(observation, candidates)['messages']
        payload = dict(model=self.model, input=messages, store=False, stream=False,
                       max_output_tokens=self.max_output_tokens,
                       text={'format': {
                           'type': 'json_schema', 'name': 'mtg_choice', 'strict': True,
                           'schema': {
                               'type': 'object', 'properties': {
                                   'choice': {'type': 'integer', 'enum': list(range(len(candidates)))}},
                               'required': ['choice'], 'additionalProperties': False}}})
        if self.reasoning_effort is not None:
            payload['reasoning'] = {'effort': self.reasoning_effort}
        return payload

    def _json_request(self, url, payload=None):
        key = os.environ.get(self.api_key_env, '').strip()
        if not key:
            raise ValueError(f'Set {self.api_key_env} to an OpenAI API key before starting')
        request = Request(url, data=json.dumps(payload).encode('utf-8') if payload is not None else None,
                          headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1048577)
        except HTTPError as exc:
            # Never echo response bodies, headers, key values or submitted state.
            raise ValueError(f'OpenAI API HTTP {exc.code}; check API access, model, quota and request settings') from None
        if len(raw) > 1048576:
            raise ValueError('OpenAI response exceeds the size limit')
        return json.loads(raw)

    def check_model(self):
        """Check credentials/model access with a GET, without generating tokens."""
        data = self._json_request('https://api.openai.com/v1/models/' + quote(self.model, safe=''))
        if not isinstance(data, dict) or not data.get('id'):
            raise ValueError('Invalid OpenAI model response')

    def _request(self, payload):
        data = self._json_request(self.endpoint, payload)
        if not isinstance(data, dict):
            raise ValueError('Invalid OpenAI response object')
        usage = data.get('usage') or {}
        if not isinstance(usage, dict):
            raise ValueError('Invalid OpenAI usage object')
        for name in ('input_tokens', 'output_tokens', 'total_tokens'):
            value = usage.get(name, 0)
            if type(value) is int and value >= 0:
                self.usage[name] += value
        if data.get('status') != 'completed':
            raise ValueError('OpenAI response did not complete; check output token limit or API status')
        texts = []
        output = data.get('output', [])
        if not isinstance(output, list):
            raise ValueError('Invalid OpenAI output list')
        for item in output:
            if not isinstance(item, dict):
                raise ValueError('Invalid OpenAI output item')
            if item.get('type') != 'message':
                continue
            contents = item.get('content', [])
            if not isinstance(contents, list):
                raise ValueError('Invalid OpenAI message content')
            for content in contents:
                if not isinstance(content, dict):
                    raise ValueError('Invalid OpenAI content item')
                if content.get('type') == 'refusal':
                    raise ValueError('OpenAI model refused the decision')
                if content.get('type') == 'output_text':
                    if not isinstance(content.get('text'), str):
                        raise ValueError('Invalid OpenAI text output')
                    texts.append(content['text'])
        if not texts:
            raise ValueError('OpenAI response contains no decision text')
        return ''.join(texts)
