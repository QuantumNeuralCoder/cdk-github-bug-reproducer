import asyncio
import json
import requests
import sys
import os
import logging
import shutil
from enum import Enum
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

from .process_definitions import Process, ForEach, Task, ExecuteCode, process_context_memory_return_value

current_env = os.environ.copy()

class Memory:
    def __init__(self):
        self.current_step = 0
        self.root_dir = "/app"
        self.helper_resources_list = []
        self.helpers_to_parent_mapping = {}
        self.main_to_helpers_mapping = defaultdict(list)
        self.ordered_main_resources = []
        # self.current_iteration_helpers_file = []

    async def move_to_next_step(self, func: Callable[[str, dict], Any], overwrite_contex_memory: Dict[str, Any]) -> None:
        logger.debug("Move current_step from %s", str(self.current_step))
        self.current_step += 1
        await func("add_or_update_memory_entry", {
            "entry_key": f"current_step",
            "value": f"step{self.current_step}"
        })
        await func("add_or_update_memory_entry", {
            "entry_key": f"next_step",
            "value": f"step{self.current_step+1}"
        })
        logger.debug("current_step moved to %s", str(self.current_step))
        # create next_step directory under root_dir, make sure that the directory will be created only if it does not exist to avoid repleacement of existence data
        if not os.path.exists(os.path.join(self.root_dir, f"step{self.current_step+1}")):
            os.mkdir(os.path.join(self.root_dir, f"step{self.current_step+1}"))

    async def get_list_of_resources(self, func: Callable[[str, dict], Any]) -> None:
        logger.debug("Get list of resources defined in %s", f"{self.root_dir}/step{str(self.current_step)}")
        path = os.path.join(self.root_dir, f"step{self.current_step}")
        resource_list = []
        for filename in os.listdir(path):
            # Check if file ends with '.json'
            if filename.endswith(".json"):
                # Strip off '.json' and build the desired string
                resource_name = f"AWS::VPCLATTICE::{filename[:-5]}".upper()
                resource_list.append(resource_name)
        await func("add_or_update_memory_entry", {
            "entry_key": f"list_of_aws_resources_to_analyze_for_helpers",
            "value": resource_list
        })

    async def get_helpers_data(self, func: Callable[[str, dict], Any], overwrite_contex_memory: Dict[str, Any]) -> None:
        logger.info("get list of helper resources and map to main resources")

        path = os.path.join(self.root_dir, f"step{self.current_step}")
        self.helper_resources_list = []
        for filename in os.listdir(path):
            is_helper_resource_response = await func("read_memory_entry", {
                "entry_key": f"{filename}_is_helper_resource"
            })
            is_helper_resource = process_context_memory_return_value(is_helper_resource_response)
            if is_helper_resource.lower() == "true":
                self.helper_resources_list.append(filename)
                parent_resources_response = await func("read_memory_entry", {
                    "entry_key": f"{filename}_helper_parent_resources"
                })
                parent_resources = process_context_memory_return_value(parent_resources_response).split("\n")
                for resource in parent_resources:
                    if not os.path.exists(
                            f"{self.root_dir}/step{self.current_step}/{resource.lower().replace('aws::vpclattice::', '')}.json"):
                        continue
                    self.main_to_helpers_mapping[
                        f"{resource.lower().replace('aws::vpclattice::', '')}.json"].append(filename)
        scores = {}
        all_main_resources = list(self.main_to_helpers_mapping.keys())
        for main_resource in all_main_resources:
            scores[main_resource] = len(self.helpers_to_parent_mapping.get(main_resource, []))
        self.ordered_main_resources = sorted(all_main_resources, key=lambda p: scores[p])
        logger.info("Get list of helper resources result %s, %s, %s", self.helper_resources_list, self.main_to_helpers_mapping, self.ordered_main_resources)

    async def get_current_main_resource_helpers_resources(self, func: Callable[[str, dict], Any], overwrite_contex_memory: Dict[str, Any]) -> List[str]:
        logger.debug("get_current_main_resource_helpers_resources")
        if "parent_file" in overwrite_contex_memory:
            parent_file = overwrite_contex_memory["parent_file"]
        else:
            parent_file_response = await func("read_memory_entry", {
                "entry_key": "parent_file"
            })
            parent_file = process_context_memory_return_value(parent_file_response)

        if not os.path.exists(os.path.join(self.root_dir, f"step{self.current_step + 1}", parent_file)):
            shutil.copyfile(f"{self.root_dir}/step{self.current_step}/{parent_file}", f"{self.root_dir}/step{self.current_step+1}/{parent_file}")
            logger.info("Copied parent file %s to %s", parent_file,  f"{self.root_dir}/step{self.current_step+1}/{parent_file}")

        # self.current_iteration_helpers_file = self.main_to_helpers_mapping[parent_file]

        logger.info("Get list of helper resources result %s", self.main_to_helpers_mapping[parent_file] )
        return self.main_to_helpers_mapping[parent_file]

    async def move_unprocessed_resources(self, func: Callable[[str, dict], Any], overwrite_contex_memory: Dict[str, Any] ) -> None:
        logger.debug("move_unprocessed_resources")
        path = os.path.join(self.root_dir, f"step{self.current_step}")
        for filename in os.listdir(path):
            # Check if file ends with '.json'
            if filename.endswith(".json"):
               if not os.path.exists(os.path.join(self.root_dir, f"step{self.current_step+1}", filename)):
                    logger.debug("Copy file %s to %s", filename, f"{self.root_dir}/step{self.current_step+1}/{filename}")
                    shutil.copyfile(f"{self.root_dir}/step{self.current_step}/{filename}", f"{self.root_dir}/step{self.current_step+1}/{filename}")
        logger.debug("move_unprocessed_resources is done")


class IssueType(Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    BOTH_BUG_AND_FEATURE_REQUEST = "both_bug_and_feature"


@dataclass
class IssueComment:
    comment_body: str
    comment_author: str


@dataclass
class GithubIssue:
    issue_number: str
    issue_title: str
    issue_body: str
    issue_comments: List[IssueComment]
    issue_type: IssueType
    issue_summary: Optional[str] = None

    def generate_issue_prompt(self):
        issue_comments = "\n".join(
            [
                f"""
Comment:
Comment Author: {comment.comment_author}
Comment Body:
{comment.comment_body}
#################################################
"""
                for comment in self.issue_comments
            ]
        )
        return f"""
<github_issue>

issue Title: {self.issue_title}

issue Body:
{self.issue_body}

Issue Comments:
{issue_comments}
</github_issue>
"""

BUG_LABEL = "bug"
FEATURE_REQUEST_LABEL = "feature-request"


def retrieve_github_issue(repo:str, issue_number: str, gh_token:str) -> GithubIssue:
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    headers = {"Authorization": f"token {gh_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        issue_data = response.json()
        return GithubIssue(
            issue_number=issue_data["number"],
            issue_title=issue_data["title"],
            issue_body=issue_data["body"],
            issue_comments=_retrieve_issue_comments(issue_data, gh_token),
            issue_type=_retrieve_issue_type(issue_data),
        )
    else:
        raise Exception(f"Failed to fetch issue {issue_number}: {response.status_code}")

def _retrieve_issue_comments(issue_data, gh_token) -> List[IssueComment]:
    if issue_data["comments"] <= 0:
        return []
    headers = {"Authorization": f"token {gh_token}"}
    issue_comments = []
    comments_url = issue_data["comments_url"]
    comments_response = requests.get(comments_url, headers=headers)
    if comments_response.status_code == 200:
        comments = comments_response.json()
        for comment in comments:
            issue_comments.append(IssueComment(comment["body"], comment["user"]["login"]))
    return issue_comments

def _retrieve_issue_type(issue_data):
    all_labels = [label["name"] for label in issue_data["labels"]]
    is_bug = BUG_LABEL in all_labels
    is_feature = FEATURE_REQUEST_LABEL in all_labels
    if is_bug and is_feature:
        return IssueType.BOTH_BUG_AND_FEATURE_REQUEST
    return IssueType.BUG if is_bug else IssueType.FEATURE_REQUEST


async def process(issue_id, repo_name, role_arn, gh_token):
    # Get user input and check for exit commands
    memory = Memory()

    issue = retrieve_github_issue(repo_name, issue_id, gh_token)
    if issue.issue_type != IssueType.BUG:
        logger.info("Issue %s is not a bug, it is a %s", issue_id, issue.issue_type)
        logger.info("No need to continue processing. Exiting...")
        sys.exit(0)

    process: Process = Process(
        process_id=f"Create Bug Reproducing CDK App for issue {issue_id}",
        description="""
You are an AWS CDK expert, and helping AWS CDK team to create a CDK application that the team can use to reproduce a github issue created by a customer.
""",
        guidelines=[],
        steps=[
            Task(
                step_id="step1",
                summary="Given a github issue that represent a bug, create a CDK application that reproduce the customer issue.",
                description="""
Given the following Github issue on aws-cdk reposiroty
${github_issue_details}

We want your help to create a CDK application that reproduce the customer issue, and save it in
${cdk_app_root_path}/gh_issue_${issue_id}

To create a CDK application, in an empty directory, and ALWAYS start with the command `cdk init --language=<<language you defined .. choose between
[csharp|fsharp|go|java|javascript|python|typescript]`. Then you can update the generated application as following for each language:
 - For python, you will a directory with the same name as the directory name where you executed the init command, and in this directory,
   you will find a python file with suffix (`_stack.py`) and the remaining is actually also the directory name where you executed the init command.
   This file contains the Stack, where you can edit the __init__ function to add the logic to reproduce the customer issue.
   You also should update the `requirements` file to add the required aws-cdk-lib version to reproduce the customer issue. You will find
   the `requirements` file in the directory where you executed the init command.
 - For typescript, you will find a directory named `lib`, and in the directory where you executed the init command. In this directory
   you will find a file with suffix (`-stack.ts`) and the remaining is actually also the directory name where you executed the init command.
   This file contains the Stack, where you can edit the constructor to add the logic to reproduce the customer issue.
   You also should update the `package.json` file to add the required aws-cdk-lib version to reproduce the customer issue. You will find
   the `package.json` file in the directory where you executed the init command.

<Guidelines>
 - Create the CDK application using the same language that the customer mentioned in the github issue.
 - Use the same aws-cdk-lib version that the customer mentioned in the github issue. If the issue in an alpha module,
   so also use the alpha module version the customer mentioned. If no version mentioned, so use the latest release version.
 - Make sure to use the same feature flags if the customer mentioned any in the issue.
 - Add a readme file in the created project to tell the team what is the expected result when they use this project.
   Will it fail while running `cdk synth` command, will it fail while running `cdk deploy`, or it will fail after successful deployment,
   but will not support specific use case. Also, add the exact expected error.
 - Do Not build the application or run any reproducing steps. STOP AFTER CREATING THE APPLICATION.
</Guidelines>

I want you to reply back with the path where I can find the created CDK Application, the application name you choose for the cdk application, and the readme path.
"""
            ),
            Task(
                step_id="step2",
                summary="Given the CDK Application generated in previous step, I want to verify if it reproduce the reported issue or not",
                description="""
Given the CDK Application created in previous step. You can get more details from the previous step output:
<previous_step_output>
${step1_response}
</previous_step_output>

please find the created application, and do the reproducing steps to check if it is really reproduce the reported issue or not.

Reproducing steps will ALWAYS start by building the CDK application depends on the language used for it, see the following:
 - If it is written in Typescript or javascript, you can build it using `npm install && npm run build`.
 - If it is python, run `pip install -r <<requirements file>>`

Then determine if the issue reported happen after synthesizing the template, so you should run `cdk synth` command, and then check
the generated template in `cdk.out` directory, the template name will have a prefix with the application name.

If the issue happen only after deployment, you can run `cdk deploy` and check the errors returned from the command to make sure these are the expected errors.

If the issue happen in runtime after everything got deployed successfully, so based on the issue, please run the `aws cli` commands that you can use to reproduce the issue.

If the application reproduce the issue, please update the README file with the verification that the application is really reproducing the issue.
If the issue was in deployment or runtime, please add in Readme the stack name you created, and then the `aws cli` commands you executed to confirm the issue.

<Guidelines>
 - Make sure that you switch to the application path before run any command or ask to list any directories, or check the directory structure.
 - Use AWS Documentation search tool to search for errors you face while building, synthesizing, and deployment.
   Then whatever url returned from it, use fetch tool to read that url, and based on it, decide how to fix your issue.
</Guidelines>
""",
            ),
        ],
        inputs={
            "cdk_app_root_path": "/app/gh_issues",
            "github_issue_details": issue.generate_issue_prompt(),
            "issue_id": issue_id,
        },
        profiles=[role_arn],
    )

    await process.run()