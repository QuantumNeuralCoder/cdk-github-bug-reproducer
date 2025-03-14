from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typing import Any, List
import logging

logger = logging.getLogger(__name__)

class MCPClients:
    def __init__(self, servers_params: List[StdioServerParameters]):
        self.clients = [MCPClient(server_param) for server_param in servers_params]

    async def __aenter__(self):
        logger.info("entering ...")
        for client in self.clients:
            await client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logger.info("exiting ...")
        for client in self.clients:
            await client.__aexit__(exc_type, exc_val, exc_tb)

    async def get_available_tools(self) -> List[Any]:
        tools = []
        for client in self.clients:
            tools.append({
                "call_tool": client.call_tool,
                "tools_list": await client.get_available_tools()
            })
        return tools

class MCPClient:
    def __init__(self, server_params: StdioServerParameters):
        self.server_params = server_params
        self.session = None
        self._client = None
        
    async def __aenter__(self):
        """Async context manager entry"""
        logger.info(f"entering command {self.server_params.command} {self.server_params.args} ...")
        await self.connect()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logger.info(f"exiting {self.server_params.command} {self.server_params.args}...")
        """Async context manager exit"""
        if self.session:
            logger.info(f"exiting {self.server_params.command} {self.server_params.args} session ...")
            await self.session.__aexit__(exc_type, exc_val, exc_tb)
        if self._client:
            logger.info(f"exiting {self.server_params.command} {self.server_params.args} client ...")
            await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def connect(self):
        """Establishes connection to MCP server"""
        self._client = stdio_client(self.server_params)
        self.read, self.write = await self._client.__aenter__()
        session = ClientSession(self.read, self.write)
        self.session = await session.__aenter__()
        await self.session.initialize()

    async def get_available_tools(self) -> List[Any]:
        """List available tools"""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")
            
        tools = await self.session.list_tools()
        return tools.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call a tool with given arguments"""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")
            
        result = await self.session.call_tool(tool_name, arguments=arguments)
        return result
