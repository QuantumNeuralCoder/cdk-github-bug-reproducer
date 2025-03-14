from typing import Any
import json
import logging
import uuid
from mcp.server.fastmcp import FastMCP

import boto3
import requests
from time import sleep
from time import time as current_time
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError, ReadTimeoutError
import logging
from functools import lru_cache, wraps
from botocore.credentials import RefreshableCredentials
from botocore.session import get_session
import datetime

# Initialize FastMCP server
mcp = FastMCP("mynah_mcp2")

# Do not execute this method multiple times
@lru_cache(maxsize=1)
def setup_logging(level=logging.INFO):
    """
    Set up logging configuration for the application.
    This includes configuring the log level, log format, and log handlers.
    """

    # Configure the root logger because loggers inherit their effective level from their parent loggers if their own level isn't explicitly set.
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            # logging.FileHandler("app.log"),
            logging.StreamHandler()  # Output to console
        ],
    )

    # Silence boto3 logs below WARNING level
    for logger_name in ("boto3", "botocore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

def get_refreshable_session(region_name="us-east-1"):
    """
    Create a boto3 session with refreshable credentials using role assumption.
    The credentials will auto-refresh when they expire.
    """
    def refresh_credentials():
        """Get temporary credentials using STS AssumeRole"""
        logger.info(f"Refreshing credentials by assuming role: {ROLE_ARN}")
        sts_client = boto3.client('sts')
        response = sts_client.assume_role(
            RoleArn=ROLE_ARN,
            RoleSessionName=ROLE_SESSION_NAME,
            DurationSeconds=ROLE_SESSION_DURATION
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
    botocore_session.set_config_variable('region', region_name)
    
    # Create a boto3 session from the botocore session
    return boto3.Session(botocore_session=botocore_session)

logger = get_logger(__name__)

# Constants
DEFAULT_AWS_PROFILE = "ngde-abstractions-bedrock"
ROLE_ARN = "arn:aws:iam::654654263977:role/Admin"
ROLE_SESSION_NAME = "MynahSearchSession"
ROLE_SESSION_DURATION = 10800  # 3 hours in seconds

def custom_retry_decorator(max_retries=5, initial_wait_time=1, backoff_factor=1.5):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            wait_time = initial_wait_time
            start_time = current_time()

            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    total_time = current_time() - start_time

                    if isinstance(e, ClientError):
                        error_code = e.response["Error"]["Code"]
                        retryable_errors = [
                            "ThrottlingException",
                            "RequestLimitExceeded",
                            "InternalServerErrorException",
                            "ServiceUnavailableException",
                            "TooManyRequestsException",
                            "ModelStreamLimitExceededException",
                            "ModelTimeoutException",
                            "ModelNotReadyException",
                            "ResourceInUseException",
                            "ModelCapacityExceededException",
                        ]
                        if error_code not in retryable_errors:
                            logger.error(f"Non-retryable error: {error_code}")
                            raise

                    elif not isinstance(e, (ConnectionError, TimeoutError, ReadTimeoutError)):
                        # If it's not a known retryable error, log and re-raise
                        logger.error(f"Non-retryable error({type(e).__name__}): {str(e)}")
                        raise

                    if retries < max_retries:
                        logger.info(
                            f"Retryable error encountered. Attempt {retries} of {max_retries}. "
                            f"Retrying in {wait_time} seconds... Error: {str(e)}"
                        )
                        sleep(wait_time)
                        wait_time = min(wait_time * backoff_factor, 128)  # Cap at 128 seconds
                    else:
                        logger.error(f"Max retries reached. Total time: {total_time:.2f} seconds. Error: {str(e)}")
                        raise

            logger.error(
                f"Max retries reached without successful completion. "
                f"Total time: {current_time() - start_time:.2f} seconds."
            )
            raise Exception("Max retries reached")

        return wrapper

    return decorator


#################### MYNAH SERVICE - CLIENT IMPLEMENTATION ####################

# Mynah Model

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class SearchType(Enum):
    TEXT = "text"
    CODE = "code"

    def __str__(self):
        return self.value


class ProgrammingLanguage(Enum):
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"


class SuggestionBodyType(Enum):
    HTML = "Html"
    HIGHLIGHTED_HTML = "HighlightedHtml"
    MARKDOWN = "Markdown"
    RAW_TEXT = "RawText"


class SuggestionType(Enum):
    LEXICAL_SUGGESTION = "LexicalSuggestion"
    NEURAL_SUGGESTION = "NeuralSuggestion"
    CURATED_SUGGESTION = "CuratedSuggestion"


@dataclass
class MynahConfig:
    aws_profile: Optional[str]
    region: str = "us-east-1"
    endpoint: str = "https://knowledge-search.us-east-1.gamma.mynah.aws.dev/general/search"
    service_name: str = "mynah-search"
    requester: str = "RagExperimental"
    max_results: int = 5
    is_code_search_enabled: bool = False
    default_language: ProgrammingLanguage = ProgrammingLanguage.TYPESCRIPT
    accept_suggestion_body: SuggestionBodyType = SuggestionBodyType.HTML


@dataclass
class TextQuery:
    input: str


@dataclass
class CodeQuery:
    code: str
    language: ProgrammingLanguage
    codeStructure: Optional[dict] = None


@dataclass
class ContextAttribute:
    key: str
    value: str


@dataclass
class TextExcerptSuggestion:
    link: str
    title: str
    suggestionBody: str
    summary: Optional[str] = None
    context: Optional[List[ContextAttribute]] = None
    type: Optional[SuggestionType] = None
    metadata: Optional[dict] = None
    sourceCreatedAt: Optional[int] = None
    sourceUpdatedAt: Optional[int] = None


@dataclass
class SearchRequest:
    contextAttributes: List[ContextAttribute]
    requester: str
    textQuery: Optional[TextQuery] = None
    codeQuery: Optional[CodeQuery] = None
    maxResults: Optional[int] = 100
    acceptSuggestionBody: Optional[SuggestionBodyType] = SuggestionBodyType.MARKDOWN
    requestId: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert the request to a dictionary, excluding None values"""
        result: dict = {
            "contextAttributes": [{"key": attr.key, "value": attr.value} for attr in self.contextAttributes],
            "requester": self.requester,
        }

        if self.textQuery:
            result["textQuery"] = {"input": self.textQuery.input}

        if self.codeQuery:
            result["codeQuery"] = {"code": self.codeQuery.code, "language": self.codeQuery.language.value}
            if self.codeQuery.codeStructure:
                result["codeQuery"]["codeStructure"] = self.codeQuery.codeStructure

        if self.maxResults:
            result["maxResults"] = self.maxResults

        if self.acceptSuggestionBody:
            result["acceptSuggestionBody"] = self.acceptSuggestionBody.value

        if self.requestId:
            result["requestId"] = self.requestId

        return result


@dataclass
class SearchResponse:
    queryId: Optional[str]
    suggestions: List[Dict[str, TextExcerptSuggestion]]
    facets: Optional[Dict[str, List[str]]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SearchResponse":
        """Create a SearchResponse instance from a dictionary"""
        suggestions = []
        if "suggestions" in data:
            for suggestion in data["suggestions"]:
                if "textExcerptSuggestion" in suggestion:
                    text_suggestion = suggestion["textExcerptSuggestion"]
                    # Convert the nested textExcerptSuggestion to our model
                    excerpt = TextExcerptSuggestion(
                        link=text_suggestion.get("link", ""),
                        title=text_suggestion.get("title", ""),
                        suggestionBody=text_suggestion.get("suggestionBody", ""),
                        summary=text_suggestion.get("summary"),
                        context=(
                            [ContextAttribute(**ctx) for ctx in text_suggestion.get("context", [])]
                            if "context" in text_suggestion
                            else None
                        ),
                        type=SuggestionType(text_suggestion["type"]) if "type" in text_suggestion else None,
                        metadata=text_suggestion.get("metadata"),
                        sourceCreatedAt=text_suggestion.get("sourceCreatedAt"),
                        sourceUpdatedAt=text_suggestion.get("sourceUpdatedAt"),
                    )
                    suggestions.append({"textExcerptSuggestion": excerpt})

        return cls(queryId=data.get("queryId"), suggestions=suggestions, facets=data.get("facets"))


class MynahJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, SearchRequest):
            return obj.to_dict()
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return super().default(obj)


# Mynah Service


class MynahSearchService:
    def __init__(self, config: MynahConfig):
        # Use refreshable credentials with role assumption instead of profile
        self.session = get_refreshable_session(region_name=config.region)
        logger.info(f"Created session with refreshable credentials for region {config.region}")
        
        self.region = config.region
        self.endpoint = config.endpoint
        self.service_name = config.service_name
        self.requester = config.requester
        self.max_results = config.max_results
        self.is_code_search_enabled = config.is_code_search_enabled
        self.default_language = config.default_language
        self.accept_suggestion_body = config.accept_suggestion_body

    def search_aws_qna(
        self,
        query: str,
        max_results: Optional[int] = None,
    ) -> SearchResponse:
        return self.search(
            query,
            max_results,
            context_attributes=[
                ContextAttribute(key="document-type", value="documentation"),
                ContextAttribute(key="document-type", value="question-answer"),
                ContextAttribute(key="document-type", value="blog"),
                ContextAttribute(key="document-type", value="faq"),
            ],
        )

    def search_cloud_formation(
        self,
        query: str,
        max_results: Optional[int] = None,
    ) -> SearchResponse:
        return self.search(
            query,
            max_results,
            context_attributes=[
                ContextAttribute(key="domain", value="docs.aws.amazon.com"),
                ContextAttribute(key="aws-docs-search-product", value="AWS CloudFormation"),
                ContextAttribute(key="documentation-type", value="cloudformation"),
            ],
        )

    def search_aws_docs(
        self,
        query: str,
        max_results: Optional[int] = None,
        include_faq: Optional[bool] = False,
        include_blog: Optional[bool] = False,
    ) -> SearchResponse:
        context_attributes = [
            ContextAttribute(key="domain", value="docs.aws.amazon.com"),
            ContextAttribute(key="document-type", value="documentation"),
        ]

        if include_blog:
            context_attributes.append(ContextAttribute(key="document-type", value="blog"))

        if include_faq:
            context_attributes.append(ContextAttribute(key="document-type", value="faq"))

        return self.search(
            query,
            max_results,
            context_attributes=context_attributes,
        )

    @custom_retry_decorator()
    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        context_attributes: List[ContextAttribute] = [],
        search_type: SearchType = SearchType.TEXT,
    ) -> SearchResponse:
        """Perform a search query and return results"""
        max_results = max_results if max_results is not None else self.max_results
        context_attributes = (
            context_attributes
            if context_attributes
            else [
                ContextAttribute(key="document-type", value="documentation"),
                ContextAttribute(key="document-type", value="question-answer"),
                # ContextAttribute(key="document-type", value="code"),
                ContextAttribute(key="document-type", value="blog"),
                ContextAttribute(key="document-type", value="faq"),
            ]
        )
        search_request = self._create_search_request(query, max_results, search_type, context_attributes)
        signed_headers = self._get_signed_headers(search_request)

        return self._make_search_request(signed_headers, search_request)

    def _create_search_request(
        self, query: str, max_results: int, search_type: SearchType, context_attributes: List[ContextAttribute]
    ) -> SearchRequest:
        """Create the search request payload"""

        logger.debug(f"Performing {search_type} search with query '{query}'")

        if self.is_code_search_enabled and search_type == SearchType.CODE:
            request = SearchRequest(
                contextAttributes=[],
                requester=self.requester,
                codeQuery=CodeQuery(code=query, language=self.default_language),
                maxResults=max_results,
                acceptSuggestionBody=self.accept_suggestion_body,
                requestId=str(uuid.uuid4()),
            )
        else:
            request = SearchRequest(
                contextAttributes=context_attributes,
                requester=self.requester,
                textQuery=TextQuery(input=query),
                maxResults=max_results,
                acceptSuggestionBody=self.accept_suggestion_body,
                requestId=str(uuid.uuid4()),
            )

        return request

    def _get_signed_headers(self, search_request: SearchRequest) -> Dict[str, str]:
        """Get AWS signed headers for the request"""
        headers = {
            "Content-Type": "application/json",
            "x-amzn-requester": search_request.requester,
        }

        if search_request.requestId:
            headers["x-amzn-requestid"] = search_request.requestId

        request_json = json.dumps(search_request, cls=MynahJSONEncoder)

        credentials = self.session.get_credentials()
        if credentials is None:
            raise ValueError("No AWS credentials found")

        aws_request = AWSRequest(method="POST", url=self.endpoint, data=request_json, headers=headers)

        SigV4Auth(credentials, self.service_name, self.region).add_auth(aws_request)
        return dict(aws_request.headers)

    def _make_search_request(self, signed_headers: Dict[str, str], search_request: SearchRequest) -> SearchResponse:
        """Make the HTTP request to the search endpoint and return a SearchResponse"""
        # print(f"Request with the data:\n{json.dumps(search_request, cls=MynahJSONEncoder)}")
        response = requests.post(
            self.endpoint, headers=signed_headers, data=json.dumps(search_request, cls=MynahJSONEncoder)
        )
        response.raise_for_status()
        return SearchResponse.from_dict(response.json())


class SearchResultsFormatter:
    def format_suggestions(self, response: SearchResponse) -> str:
        """Format search suggestions and return them as a string"""
        formatted_results = []

        if not response.suggestions:
            return "No results found."

        for idx, suggestion in enumerate(response.suggestions, 1):
            if "textExcerptSuggestion" in suggestion:
                text_suggestion = suggestion["textExcerptSuggestion"]
                formatted_results.append(self.format_suggestion(text_suggestion, idx))

        return "\nSearch Results:" + "".join(formatted_results)

    def format_suggestion(self, text_suggestion: TextExcerptSuggestion, idx: Optional[int] = None) -> str:
        """Format search suggestion and return it as a string"""
        return f"""
{idx if idx else ""} Title: {text_suggestion.title}
   Link: {text_suggestion.link}
   Summary: {text_suggestion.summary if text_suggestion.summary else 'N/A'}
   {'-' * 80}
   Suggestion Body: {text_suggestion.suggestionBody}
   {'-' * 80}
"""

config = MynahConfig(aws_profile=None, accept_suggestion_body=SuggestionBodyType.MARKDOWN)
search_service = MynahSearchService(config)
formatter = SearchResultsFormatter()

@mcp.tool()
async def search_aws_documentations(query: str, max_results: int = 5) -> str:
    """Search Different Websites like AWS Documentation, AWS Blogs, AWS Products FAQs, and stackoverflow.

    Args:
        query: the query string to be used to search in websites like AWS Documentation, AWS Blogs, AWS Products FAQs, and stackoverflow
        max_results: the maximum result to be returned. Its default value is 5.
    """
    response = search_service.search(query=query, max_results=max_results)
    formatted_results = formatter.format_suggestions(response)
    return formatted_results
    

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
