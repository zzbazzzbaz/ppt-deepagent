from langchain.chat_models import init_chat_model

from agent.settings import deepseek_settings, qwen_settings

deepseek_model = init_chat_model(
    model=deepseek_settings.model,
    model_provider=deepseek_settings.model_provider,
    base_url=deepseek_settings.base_url,
    api_key=deepseek_settings.api_key,
    timeout=60,
)

qwen_model = init_chat_model(
    model=qwen_settings.model,
    model_provider=qwen_settings.model_provider,
    base_url=qwen_settings.base_url,
    api_key=qwen_settings.api_key,
    timeout=60,
)
