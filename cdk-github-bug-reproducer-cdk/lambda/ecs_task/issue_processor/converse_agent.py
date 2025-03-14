import time

import boto3, json, re
from botocore.config import Config
import logging
from botocore.credentials import RefreshableCredentials
from botocore.session import get_session
import datetime

logger = logging.getLogger(__name__)

def get_refreshable_session_from_role(role_arn, region='us-west-2', session_name='ConverseAgentSession', duration_seconds=3600):
    """
    Create a boto3 session with refreshable credentials using role assumption.
    The credentials will auto-refresh when they expire.

    Args:
        role_arn: The ARN of the role to assume
        region: AWS region
        session_name: Name for the role session
        duration_seconds: Duration of the session in seconds (max 3 hours = 10800)

    Returns:
        A boto3 session with refreshable credentials
    """
    def refresh_credentials():
        """Get temporary credentials using STS AssumeRole"""
        logger.info(f"Refreshing credentials by assuming role: {role_arn}")
        sts_client = boto3.client('sts')
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=duration_seconds
        )

        credentials = response['Credentials']
        logger.info(f"Credentials refreshed, valid until: {credentials['Expiration']}")

        return {
            'access_key': credentials['AccessKeyId'],
            'secret_key': credentials['SecretAccessKey'],
            'token': credentials['SessionToken'],
            'expiry_time': credentials['Expiration'].isoformat()
        }

    # Create refreshable credentials
    refreshable_credentials = RefreshableCredentials.create_from_metadata(
        metadata=refresh_credentials(),
        refresh_using=refresh_credentials,
        method='sts-assume-role'
    )

    # Create a botocore session with the refreshable credentials
    botocore_session = get_session()
    botocore_session._credentials = refreshable_credentials
    botocore_session.set_config_variable('region', region)

    # Create a boto3 session from the botocore session
    return boto3.Session(botocore_session=botocore_session)

class ConverseAgent:
    def __init__(self, model_id, profile, enable_Reasoning:bool, region='us-west-2', system_prompt='You are a helpful assistant.'):
        logger.info(f"Creating a new converse agent using profile/role {profile}, and model {model_id}, and region {region}")
        self.model_id = model_id
        self.region = region
        self.profile = profile
        config = Config(
            read_timeout=2000,
            retries={
                'max_attempts': 4,
                'mode': 'standard'
            }
        )

        # Check if profile is a role ARN (starts with "arn:aws:iam::")
        if profile and profile.startswith("arn:aws:iam::"):
            # Use role assumption with refreshable credentials
            logger.info(f"Using role assumption with ARN: {profile}")
            session = get_refreshable_session_from_role(
                role_arn=profile,
                region=region,
                session_name='ConverseAgentSession',
                duration_seconds=3600  # 3 hours
            )
        else:
            # Fall back to profile-based authentication
            logger.info(f"Using profile-based authentication: {profile}")
            session = boto3.Session(profile_name=profile)

        self.client = session.client('bedrock-runtime', region_name=self.region, config=config)
        self.system_prompt = system_prompt
        self.messages = []
        self.tools = None
        self.enable_Reasoning = enable_Reasoning
        self.response_output_tags = [] # ['<response>', '</response>']

    async def invoke_with_prompt(self, prompt):
        content = [
            {
                'text': prompt
            }
        ]
        return await self.invoke(content)

    async def invoke(self, content):

        logger.info(f"Profile: {self.profile} User: {json.dumps(content, indent=2)}")

        self.messages.append(
            {
                "role": "user",
                "content": content
            }
        )
        while True:
            try:
                response = self._get_converse_response()
                break
            except self.client.exceptions.ThrottlingException as e:
                logger.info(f"ThrottlingException using model {self.model_id}, and profile {self.profile}: {e}")
                time.sleep(5)
            except self.client.exceptions.ModelTimeoutException as e:
                logger.info(f"ModelTimeoutException using model {self.model_id}, and profile {self.profile}: {e}")
                time.sleep(5)

        logger.info(f"profile {self.profile} Agent: {json.dumps(response, indent=2)}")

        return await self._handle_response(response)

    def _get_converse_response(self):
        """
        https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime/client/converse.html
        """

        try:
            if self.enable_Reasoning:
                logger.info("send converse api with reasoning")
                response = self.client.converse(
                    modelId=self.model_id,
                    messages=self.messages,
                    system=[
                        {
                            "text": self.system_prompt
                        }
                    ],
                    inferenceConfig={
                        "maxTokens": 64000,
                    },
                    additionalModelRequestFields={
                        "reasoning_config": {
                            "type": "enabled",
                            "budget_tokens": 4000
                        },
                    },
                    toolConfig=self.tools.get_tools()
                )
                logger.info("received response from converse api with reasoning")
            else:
                logger.info("send converse api")
                response = self.client.converse(
                    modelId=self.model_id,
                    messages=self.messages,
                    system=[
                        {
                            "text": self.system_prompt
                        }
                    ],
                    inferenceConfig={
                        "temperature": 0.7,
                    },
                    toolConfig=self.tools.get_tools()
                )
                logger.info("received response from converse api")
            return(response)
        except Exception as e:
            logger.info(f"profile {self.profile} ,Error invoking model: {e}")
            raise e

    async def _handle_response(self, response):
        # Add the response to the conversation history
        self.messages.append(response['output']['message'])

        # Check if any of the dicts exist in response['output']['message']['content']  array contains a key `reasoningContent`, and if yes return this dict
        reasoning_content = None
        for content_item in response.get('output', {}).get('message', {}).get('content',[]):
            if 'reasoningContent' in content_item:
                reasoning_content = content_item['reasoningContent']
                break
        if reasoning_content is not None:
            logger.info(f"Reasoning:\n{reasoning_content['reasoningText'].get('text', "------")}")

        # Do we need to do anything else?
        stop_reason = response['stopReason']

        if stop_reason in ['end_turn', 'stop_sequence']:
            # Safely extract the text from the nested response structure
            try:
                message = response.get('output', {}).get('message', {})
                content = message.get('content', [])
                text = None
                for content_item in content:
                    if 'text' in content_item:
                        text = content_item['text']
                        break
                if hasattr(self, 'response_output_tags') and len(self.response_output_tags) == 2:
                    pattern = f"(?s).*{re.escape(self.response_output_tags[0])}(.*?){re.escape(self.response_output_tags[1])}"
                    match = re.search(pattern, text)
                    if match:
                        return match.group(1)
                return text
            except (KeyError, IndexError):
                return ''

        elif stop_reason == 'tool_use':
            try:
                # Extract tool use details from response
                tool_response = []
                for content_item in response['output']['message']['content']:
                    if 'toolUse' in content_item:
                        tool_request = {
                            "toolUseId": content_item['toolUse']['toolUseId'],
                            "name": content_item['toolUse']['name'],
                            "input": content_item['toolUse']['input']
                        }
                        try:
                            tool_result = await self.tools.execute_tool(tool_request)
                            tool_response.append({'toolResult': tool_result})
                        except ValueError as e:
                            if str(e).startswith("Unknown tool: "):
                                tool_response.append({'Error': e})
                            else:
                                raise e

                return await self.invoke(tool_response)

            except KeyError as e:
                raise ValueError(f"Missing required tool use field: {e}")
            except Exception as e:
                raise ValueError(f"Failed to execute tool: {e}")

        elif stop_reason == 'max_tokens':
            # Hit token limit (this is one way to handle it.)
            time.sleep(10)
            await self.invoke_with_prompt('Please continue.')

        else:
            raise ValueError(f"Unknown stop reason: {stop_reason}")

