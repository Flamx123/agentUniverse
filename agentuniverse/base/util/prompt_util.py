# !/usr/bin/env python3
# -*- coding:utf-8 -*-

# @Time    : 2024/4/16 14:42
# @Author  : wangchongshi
# @Email   : wangchongshi.wcs@antgroup.com
# @FileName: prompt_util.py
import asyncio
from typing import List

from langchain.chains.summarize import load_summarize_chain
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate

from agentuniverse.llm.llm import LLM
from agentuniverse.llm.llm_manager import LLMManager
from agentuniverse.prompt.prompt_manager import PromptManager
from agentuniverse.prompt.prompt_model import AgentPromptModel
from agentuniverse.prompt.enum import PromptProcessEnum


def summarize_by_stuff(texts: List[str], llm: LLM, summary_prompt_version: str):
    """
    stuff summarization -- general method
    """
    summary_prompt = PromptManager().get_instance_obj(summary_prompt_version)
    stuff_chain = load_summarize_chain(llm.as_langchain(), chain_type='stuff', verbose=True,
                                       prompt=summary_prompt.as_langchain())
    return asyncio.run(stuff_chain.arun([Document(page_content=text) for text in texts]))


def summarize_by_map_reduce(texts: List[str], llm: LLM, agent_llm: LLM, summary_prompt_version: str,
                            combine_prompt_version: str):
    """
    map reduce summarization -- general method
    """
    texts = split_texts(texts, agent_llm)
    summary_prompt = PromptManager().get_instance_obj(summary_prompt_version)
    combine_prompt = PromptManager().get_instance_obj(combine_prompt_version)
    map_reduce_chain = load_summarize_chain(llm.as_langchain(), chain_type='map_reduce', verbose=True,
                                            map_prompt=summary_prompt.as_langchain(),
                                            combine_prompt=combine_prompt.as_langchain())
    return asyncio.run(map_reduce_chain.arun([Document(page_content=text) for text in texts]))


def split_text_on_tokens(text: str, text_token, chunk_size=800, chunk_overlap=100) -> List[str]:
    """Split incoming text and return chunks using tokenizer."""
    # calculate the number of characters represented by each token.
    char_per_token = len(text) / text_token
    chunk_char_size = int(chunk_size * char_per_token)
    chunk_char_overlap = int(chunk_overlap * char_per_token)

    result = []
    current_position = 0

    while current_position + chunk_char_overlap < len(text):
        if current_position + chunk_char_size >= len(text):
            chunk = text[current_position:]
        else:
            chunk = text[current_position:current_position + chunk_char_size]

        result.append(chunk)
        current_position += chunk_char_size - chunk_char_overlap

    return result


def split_texts(texts: list[str], agent_llm: LLM, chunk_size=800, chunk_overlap=100, retry=True) -> list[str]:
    """
    split texts into chunks with the fixed token length -- general method
    """
    try:
        split_texts_res = []
        for text in texts:
            text_token = agent_llm.get_num_tokens(text)
            split_texts_res.extend(
                split_text_on_tokens(text=text, text_token=text_token, chunk_size=chunk_size,
                                     chunk_overlap=chunk_overlap))
        return split_texts_res
    except Exception as e:
        if retry:
            return split_texts(texts=texts, agent_llm=agent_llm, retry=False)
        raise ValueError("split text failed, exception=" + str(e))


def truncate_content(content: str, token_length: int, agent_llm: LLM) -> str:
    """
    truncate the content based on the llm token limit
    """
    return str(split_texts(texts=[content], chunk_size=token_length, agent_llm=agent_llm)[0])


def generate_template(agent_prompt_model: AgentPromptModel, prompt_assemble_order: list[str]) -> str:
    """Convert the agent prompt model to an ordered list.

    Args:
        agent_prompt_model (AgentPromptModel): The agent prompt model.
        prompt_assemble_order (list[str]): The prompt assemble ordered list.
    Returns:
        list: The ordered list.
    """
    values = []
    for attr in prompt_assemble_order:
        value = getattr(agent_prompt_model, attr, None)
        if value is not None:
            values.append(value)

    return "\n".join(values)


def process_llm_token(prompt_template: PromptTemplate, profile: dict, planner_input: dict):
    """Process the prompt template based on the prompt processor.

    Args:
        prompt_template (PromptTemplate): The prompt template.
        profile (dict): The profile.
        planner_input (dict): The planner input.
    """
    llm_model: dict = profile.get('llm_model')
    llm_name: str = llm_model.get('name')

    # get the prompt processor configuration
    prompt_processor: dict = llm_model.get('prompt_processor') or dict()
    prompt_processor_type: str = prompt_processor.get('type') or PromptProcessEnum.TRUNCATE.value
    prompt_processor_llm: str = prompt_processor.get('llm') or llm_name

    # get the summary and combine prompt versions
    summary_prompt_version: str = prompt_processor.get('summary_prompt_version') or 'prompt_processor.summary_cn'
    combine_prompt_version: str = prompt_processor.get('combine_prompt_version') or 'prompt_processor.combine_cn'

    prompt_input_dict = {key: planner_input[key] for key in prompt_template.input_variables if key in planner_input}

    agent_llm: LLM = LLMManager().get_instance_obj(llm_name)
    # get the llm instance for prompt compression
    prompt_llm: LLM = LLMManager().get_instance_obj(prompt_processor_llm)

    prompt = prompt_template.format(**prompt_input_dict)
    # get the number of tokens in the prompt
    prompt_tokens: int = agent_llm.get_num_tokens(prompt)

    remaining_tokens = agent_llm.max_context_length() - agent_llm.max_tokens

    if prompt_tokens <= remaining_tokens:
        return

    process_prompt_type_enum = PromptProcessEnum.from_value(prompt_processor_type)

    # compress the background in the prompt
    content = planner_input.get('background')
    if process_prompt_type_enum == PromptProcessEnum.TRUNCATE:
        planner_input['background'] = truncate_content(content, remaining_tokens, agent_llm)
    elif process_prompt_type_enum == PromptProcessEnum.STUFF:
        planner_input['background'] = summarize_by_stuff([content], prompt_llm, summary_prompt_version)
    elif process_prompt_type_enum == PromptProcessEnum.MAP_REDUCE:
        planner_input['background'] = summarize_by_map_reduce([content], prompt_llm,
                                                              agent_llm, summary_prompt_version, combine_prompt_version)