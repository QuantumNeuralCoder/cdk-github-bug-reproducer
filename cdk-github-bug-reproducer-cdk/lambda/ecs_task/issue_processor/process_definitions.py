import asyncio
import re
import os
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from concurrent.futures.thread import ThreadPoolExecutor
import random
from typing import Any, Dict, List, Optional, Callable
from mcp import StdioServerParameters, CallToolRequest
from mcp.types import CallToolResult

from .converse_agent import ConverseAgent
from .converse_tools import ConverseToolManager
from .mcp_client import MCPClients

# Set up a module-level logger
logger = logging.getLogger(__name__)
# model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"

async def _create_agent(mcp_clients: MCPClients, profile_name:str) -> ConverseAgent:
    agent = ConverseAgent(model_id,  profile_name, True)
    agent.tools = ConverseToolManager()

    # Fetch available tools from the MCP client
    mcp_servers_tools = await mcp_clients.get_available_tools()
    # Register each available tool with the agent
    for tools in mcp_servers_tools:
        for tool in tools["tools_list"]:
            agent.tools.register_tool(
                name=tool.name,
                func=tools["call_tool"],
                description=tool.description,
                input_schema={'json': tool.inputSchema}
            )
    return agent


def process_context_memory_return_value(response: CallToolResult) ->Optional[str]:
    """
    Process the return value from the context memory tool.
    """
    if len(response.content) == 0 or response.content[0].text is None or response.content[0].text == "No entry found for the given key.":
        return None
    else:
        values = []
        for content in response.content:
            if content.text is not None:
                values.append(content.text)
        return "\n".join(values)


async def replace_context_variables(prompt: str, func: Callable[[str, dict], Any], overrwirte_contex_memory: Dict[str, Any]) -> str:
    """
    Replace ${XYZ} occurrences in the prompt string with values from context memory.

    Args:
        prompt (str): The prompt string containing ${XYZ} variables to replace
        func (Callable): Function to access memory entries

    Returns:
        str: The prompt with all variables replaced with their memory values
    """

    # Find all ${XYZ} patterns in the prompt
    variables = re.findall(r'\${([^}]+)}', prompt)

    # Replace each variable with its memory value
    for var in variables:
        try:
            if var in overrwirte_contex_memory:
                prompt = prompt.replace(f"${{{var}}}", str(overrwirte_contex_memory[var]))
                continue
            # Get value from memory
            value = await func("read_memory_entry", {
                "entry_key": var
            })

            # Replace ${var} with the value from memory
            if len(value.content) > 0 and value.content[0].text is not None and value.content[0].text != "No entry found for the given key.":
                logger.info(f"Replacing variable {var} with value: {value.content[0].text}")
                prompt = prompt.replace(f"${{{var}}}", str(value.content[0].text))
            else:
                logger.warning(f"No value found in memory for variable: {var}")

        except Exception as e:
            logger.error(f"Error replacing variable {var}: {str(e)}")

    return prompt


class Step(ABC):
    """
    Abstract base class for a Step. A Step can have a parent (another Step).
    A Step can be executed and should return a summary describing its goal or result.
    """

    def __init__(self, step_id: str, parent: Optional["Step"] = None, skippable = True):
        """
        :param step_id: mandatory and unique id to differentiate between different steps
        :param parent: An optional reference to a parent Step.
        """
        self.step_id = step_id
        self.parent = parent
        self.skippable = skippable

    async def execute(self, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profiles:List[str], overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        """
        Execute the step.
        """
        logger.info("Checking if step %s is done before or not", self.get_id(parent_id))
        value: CallToolResult = await func("read_memory_entry", {
            "entry_key": f"{self.get_id(parent_id)}_status"
        })
        if  self.skippable and len(value.content) > 0 and value.content[0].text == "done":
            logger.info("Step %s is done before", self.get_id(parent_id))
            return
        await self.actual_execute(summary, mcp_clients, prefix, func, profiles, overrwirte_contex_memory, parent_id)

        await func("add_or_update_memory_entry", {
            "entry_key": f"{self.get_id(parent_id)}_status",
            "value": "done"
        })

    @abstractmethod
    async def actual_execute(self, summary: List[str], mcp_clients: MCPClients, prefix: str,
                      func: Callable[[str, dict], Any], profiles: List[str], overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        """
        Integrate with an LLM or another system to perform the step.
        """
        pass

    @abstractmethod
    def get_summary(self, prefix: str) -> List[str]:
        """
        Return a list of lines summarizing this step.

        :param prefix: A string representing the numbering or outline level
                       (e.g. '2', '2.1', '2.2', etc.)
        :return:       A list of summary lines (strings).
        """
        pass

    def get_id(self, parent_id: str) -> str:
        prefix = f"{parent_id}__" if parent_id else ""
        return f"{prefix}{self.step_id}"


class ExecuteCode(Step):
    """
    A Step that executes code.
    """

    def __init__(self, step_id: str, code: Callable[[Any, Any], Any], skippable: bool = False, parent: Optional["Step"] = None, parameters: Dict[str, Any] = None):
        """
        :param step_id: mandatory and unique id to differentiate between different steps
        :param code:    the code to execute
        :param parent:  An optional reference to a parent Step.
        """
        super().__init__(step_id=step_id, parent=parent, skippable=skippable)
        self.code = code
        self.parameters = parameters

    async def actual_execute(self, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profiles: List[str], overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        """
        Execute the code.
        """
        logger.info("Executing code for step %s", self.get_id(parent_id))
        if self.parameters is not None:
            parameters = {
                key: await replace_context_variables(parameter, func, overrwirte_contex_memory) for key, parameter in self.parameters.items()
            }
            await self.code(func, overrwirte_contex_memory, **parameters)
        else:
            await self.code(func, overrwirte_contex_memory)
        logger.info("Executing code for step %s is done", self.get_id(parent_id))

    def get_summary(self, prefix: str) -> List[str]:
        """
        hide summary for code execution
        """
        return []


class Task(Step):
    """
    A Task is a specific type of Step representing a single unit of work
    that will be performed by an LLM.
    It contains:
      - step_id: a unique identifier
      - summary: a brief summary of the task
      - description: a detailed description passed to the LLM
      - parent: optional parent Step
    """

    def __init__(self, step_id: str, summary: str, description: str, parent: Optional[Step] = None):
        super().__init__(step_id=step_id, parent=parent)
        self.summary = summary
        self.description = description

    async def actual_execute(self, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profiles: List[str], overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        """
        Integrate with an LLM or another system to perform the task.
        For demonstration, this method simulates a response and stores it
        in messages_history.
        """
        agent = await _create_agent(mcp_clients, profiles[0])
        logger.info("Starting task %s", self.get_id(parent_id))

        prompt = f"""
{"\n".join(summary)}

## Current Step ({prefix}) Details:
{self.description}

### Current Step Guidelines:
 - Make sure to do what is required in this step till it is finished. Always, automatically continue if needed.
"""
        response = await agent.invoke_with_prompt(await replace_context_variables(prompt, func, overrwirte_contex_memory))
        await func("add_or_update_memory_entry", {
            "entry_key": f"{self.get_id(parent_id)}_response",
            "value": response
        })
        logger.info("Task %s completed, and response is %s", self.get_id(parent_id), response)


    def get_summary(self, prefix: str) -> list[str]:
        return [f"{prefix}- {self.summary}"]


class ForEach(Step):
    """
    A ForEach is a type of Step that iterates over a collection
    and executes a list of steps for each item in that collection.
    It contains:
      - step_id: a unique identifier
      - variable_name: the name to refer to the current item within the steps
      - expression: a list (or other iterable) of items to iterate over
      - body: a list of steps to execute for each item
      - parent: optional parent Step
    """

    def __init__(self,
                 step_id: str,
                 variable_name: str,
                 expression: Optional[str],
                 body: List[Step],
                 is_parallel: bool = False,
                 parent: Optional[Step] = None,
                 expression_list_func: Optional[Callable] = None,
                 expression_list_func_with_params: Optional[Callable] = None):
        super().__init__(step_id=step_id, parent=parent)
        self.variable_name = variable_name
        self.expression = expression
        self.expression_list_func = expression_list_func
        self.expression_list_func_with_params = expression_list_func_with_params
        self.is_parallel = is_parallel
        self.profile_locks = defaultdict(asyncio.Lock)

        # Assign 'self' as the parent for each body step
        self.body = body
        for step in self.body:
            step.parent = self

    def _get_iteration_id(self, parent_id:str, current_iteration_number: int) -> str:
        return f"{self.get_id(parent_id)}__{current_iteration_number}"

    async def _get_expression_list(self, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profile: str, overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        if self.expression_list_func is not None:
            self.expression_list = self.expression_list_func()
            logger.info("Step %s loop list is %s", self.get_id(parent_id), self.expression_list)
            return

        if self.expression_list_func_with_params is not None:
            self.expression_list = await self.expression_list_func_with_params(func, overrwirte_contex_memory)
            logger.info("Step %s loop list is %s", self.get_id(parent_id), self.expression_list)
            return

        logger.info("Checking if expression list is calculated before or not for step %s", self.get_id(parent_id))
        value = await func("read_memory_entry", {
            "entry_key": f"{self.get_id(parent_id)}_expression_list"
        })

        if len(value.content) > 0 and value.content[0].text is not None and value.content[0].text != "No entry found for the given key.":
            self.expression_list = [ item.text for item in value.content]
            return
        logger.info("Calculating the expression list for step %s", self.get_id(parent_id))

        agent = await _create_agent(mcp_clients, profile)
        prompt = f"""
{"\n".join(summary)}

## Current Step ({prefix}) Details:
ForEach {self.variable_name} in {self.expression} will do the following:

### Current Step Guidelines:
 - Make sure to determine the list that will be processed, and store it in context memory using key `{self.get_id(parent_id)}_expression_list`.
 - Do not do any looping or actions on this list. This will be done later.
 - If the list is for file paths, so do not return the fullpath of each file, just return the relative path to the input given path.
"""
        response = await agent.invoke_with_prompt(await replace_context_variables(prompt, func, overrwirte_contex_memory))
        logger.info("Calculating the expression list for task %s completed, and response is %s", self.get_id(parent_id), response)

        logger.info("Reading the calculated expression list for step %s", self.get_id(parent_id))
        value = await func("read_memory_entry", {
            "entry_key": f"{self.get_id(parent_id)}_expression_list"
        })

        if len(value.content) > 0 and value.content[0].text is not None and value.content[0].text != "No entry found for the given key.":
            self.expression_list = [ item.text for item in value.content]
        else:
            raise Exception("Failed to calculate expression_list")
        logger.info("Step %s loop list is %s", self.get_id(parent_id), self.expression_list)

    async def _process_iteration(self, item: Any, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profile: str, current_iteration_number: int, overrwirte_contex_memory: Dict[str, Any], parent_id:str) -> None:

        logger.info("Checking if iteration %s is done before or not", self._get_iteration_id(parent_id, current_iteration_number))

        value = await func("read_memory_entry", {
            "entry_key": f"{self._get_iteration_id(parent_id, current_iteration_number)}_iteration_status"
        })

        if len(value.content) > 0 and "done" == value.content[0].text:
            logger.info("Task %s proccessing item %s is done before", f"{self._get_iteration_id(parent_id, current_iteration_number)}", json.dumps(item))
            return

        await self._actual_process_iteration(item, summary, mcp_clients, prefix, func, profile, current_iteration_number, overrwirte_contex_memory, parent_id)

        await func("add_or_update_memory_entry", {
            "entry_key": f"{self._get_iteration_id(parent_id, current_iteration_number)}_iteration_status",
            "value": "done"
        })

        logger.info("Task %s processing item %s is done", self._get_iteration_id(parent_id, current_iteration_number), json.dumps(item))

    async def _process_iteration_wrapper(self, item: Any, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profile: str, current_iteration_number: int, overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        async with self.profile_locks[profile]:
            logger.info("start parallel processing using profile %s, for step %s", profile, self._get_iteration_id(parent_id, current_iteration_number))
            await self._process_iteration(item, summary, mcp_clients, prefix, func, profile, current_iteration_number, overrwirte_contex_memory, parent_id)
            logger.info("parallel processing using profile %s, for step %s is done", profile,
                        self._get_iteration_id(parent_id, current_iteration_number))

    async def actual_execute(self, summary: List[str], mcp_clients: MCPClients, prefix: str, func: Callable[[str, dict], Any], profiles:List[str], overrwirte_contex_memory: Dict[str, Any], parent_id: str) -> None:
        """
        Executes each step in self.body for every item in the expression.
        The context is updated with the current item under self.variable_name.
        """

        await self._get_expression_list(summary, mcp_clients, prefix, func, profiles[0], overrwirte_contex_memory, parent_id)
        current_iteration = 1
        if self.is_parallel:
            logger.info("Start processing ForEach %s in parallel", self.get_id(parent_id))
            tasks = []
            pid = 0
            for item in self.expression_list:
                tasks.append(self._process_iteration_wrapper(item, summary, mcp_clients, prefix, func, profiles[pid],
                                              current_iteration, overrwirte_contex_memory, parent_id))
                pid = (pid + 1)%len(profiles)
                current_iteration += 1
            await asyncio.gather(*tasks)
            logger.info("ForEach %s parallel processing is done", self.get_id(parent_id))
        else:
            logger.info("Start processing ForEach %s", self.get_id(parent_id))
            for item in self.expression_list:
                await self._process_iteration(item, summary, mcp_clients, prefix, func, profiles[0], current_iteration, overrwirte_contex_memory, parent_id)
                current_iteration += 1
            logger.info("ForEach %s is done", self.get_id(parent_id))

    def get_summary(self, prefix: str) -> list[str]:
        summary = [f"{prefix}- Foreach {self.variable_name} in {self.expression} will do the following:"]
        i = 1
        for step in self.body:
            step_summary = step.get_summary(f"\t{prefix}.{i}")
            i += 1
            summary.extend(step_summary)
        return summary

    async def _actual_process_iteration(self, item, summary, mcp_clients, prefix, func, profile, current_iteration_number, overrwirte_contex_memory: Dict[str, Any], parent_id: str):
        logger.info("Using Profile %s, starting task %s, processing item %s", profile, self._get_iteration_id(parent_id, current_iteration_number), json.dumps(item))
        await func("add_or_update_memory_entry", {
            "entry_key": f"{self.variable_name}",
            "value": item
        })

        updated_overrwirte_contex_memory = {
            **overrwirte_contex_memory,
            self.variable_name: item
        }

        i = 1
        for step in self.body:
            await step.execute(summary, mcp_clients, f"{prefix}.{i}", func, [profile], updated_overrwirte_contex_memory, self._get_iteration_id(parent_id, current_iteration_number))
            i += 1
        logger.info("Task %s, processing item %s is done", self._get_iteration_id(parent_id, current_iteration_number),
                    json.dumps(item))


class Process:
    """
    A Process holds:
      - process_id: a unique identifier for the process
      - description: what is the goal of the process
      - inputs: a dict containing key-value pairs representing variables/values
      - guidelines: a list of strings with instructions or rules
      - steps: a list of Step objects that define the workflow
    """

    def __init__(self,
                 process_id: str,
                 description: str,
                 inputs: Dict[str, Any],
                 guidelines: List[str],
                 steps: List[Step],
                 profiles: List[str]):
        self.process_id = process_id
        self.description = description
        self.inputs = inputs
        self.guidelines = guidelines
        self.steps = steps
        self.mcp_servers_params = []
        print(f"profiles list: {profiles}")
        self.profiles = profiles
        self._initialize_mcp_servers()

    def _initialize_mcp_servers(self):
        current_env = os.environ.copy()
        # Create server parameters for context_memory configuration
        context_memory_server_params = StdioServerParameters(
            command="uv",
            args=["--directory", "/app/agents", "run", "context_memory.py"],
            env={
                **current_env,
                "MEMORY_PATH": f"/app/agents/memory/{self.process_id}"
            }
        )
        self.mcp_servers_params.append(context_memory_server_params)
        # Create server parameters for aidd configuration
        aidd_server_params = StdioServerParameters(
            command="mcp-server-aidd",
            args=[],
            env={
                **current_env,
            }
        )
        self.mcp_servers_params.append(aidd_server_params)
        # Create server parameters for mynah configuration
        mynah_server_params = StdioServerParameters(
            command="uv",
            args=["--directory", "/app/agents", "run", "mynah_mcp2.py"],
            env={
                **current_env,
                "UV_PYTHON": "/usr/local/bin/python3.12",
            }
        )
        self.mcp_servers_params.append(mynah_server_params)

        # Create server parameters for filesystem configuration
        filesystem_server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/"],
            env={**current_env}
        )
        self.mcp_servers_params.append(filesystem_server_params)

        # Create server parameters for fetch configuration
        fetch_server_params = StdioServerParameters(
            command="uvx",
            args=["mcp-server-fetch"],
            env={**current_env}
        )
        self.mcp_servers_params.append(fetch_server_params)

    async def run(self) -> None:
        """
        Run all steps in sequence. Each step will use or update the same context,
        so the steps can share data through the 'inputs' dictionary if needed.
        """
        logger.info("Starting Process ID: %s", self.process_id)
        summary = self.get_summary()
        i = 1
        try:
            async with MCPClients(self.mcp_servers_params) as mcp_clients:
                logger.info("initialize memory with process inputs")
                mcp_servers_tools = await mcp_clients.get_available_tools()
                func = None
                # Register each available tool with the agent
                for tools in mcp_servers_tools:
                    func = tools["call_tool"]
                    found = False
                    for tool in tools["tools_list"]:
                        if tool.name == "add_or_update_memory_entry":
                            found = True
                            break
                    if found:
                        break

                for input_variable in self.inputs.keys():
                    logger.info(f"processing process input: {input_variable}")
                    await func("add_or_update_memory_entry", {
                        "entry_key": input_variable,
                        "value": self.inputs[input_variable]
                    })

                for step in self.steps:
                    await step.execute(summary, mcp_clients, str(i), func, self.profiles, {}, None)
                    i += 1
        except:
          print("An exception occurred while closing agents")

        logger.info("Completed Process ID: %s", self.process_id)

    def get_summary(self) -> List[str]:
        """
        Return a list of lines summarizing this process.
        """
        summary = [
            "You are a smart assistant that help in implementing specific process that is represented of multiple steps",
            "## Process Overview:", self.description, "## Process Guidelines:"]
        summary.extend(self.guidelines)
        summary.append(
            "You will receive first a summary for the process, then the details of the current step you should execute. Please Make sure to only execute the current step.")
        summary.append(
            "Do not loop to repeat the current step to other thing except it is mnetioned in the step details itself. For example if there are multiple files in a directory, or multiple objects in a file, but you got requested to process ONLY one file or one Object, you must only porcess that one, and do not loop on others.")
        summary.append(
            "Never Try to create some directory if it is mentionted that it is already created. Also, never try to verify if a tool did what was requested from it, like to double check if file is created, or directory is created, or a context variable storeed correctly in the context memory. All provided tools will fail and send error if something wrong happen while executing what is required from them.")
        summary.append("""
## MANDATORY TOOL USAGE
- Use filesystem tools to do any action related to the system, like check if some directory exists or not, creating directories, creating files, and so on. When You need to update file make sure to give it the whole file content, and DO NOT BE LAZY.
- Use fetch tool to get the documentation links. If the documentations refer to other link that you think it is important, please fetch it as well. You can summarize the content and store it locally to be used later to build the summary file.
- search_aws_documentations tool to search for some information from AWS websites like documentations, blogs, faq, and other website like stack over flow.
- context_memory tools (add_or_update_memory_entry, read_memory_entry, list_memory_entries, delete_memory_entry) are used to share data between the process steps. You will be required to store Information to the context memory.
- for context_memory always use add_or_update_memory_entries, read_memory_entries if you need to process multiple entries, and this will save rounds between you and the tool.
- aidd_server_params tools to run some commands like cfn-lint using execute_shell_script tool, and other commands. Make sure to give a correct commands, and to make sure to change directory to the correct directory where you want to the main command to be executed.
        """)
        #summary.append("\n\n## Process Steps:")
        #i = 1
        #for step in self.steps:
        #    step_summary = step.get_summary(str(i))
        #    i += 1
        #   summary.extend(step_summary)
        return summary
