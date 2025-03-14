from mcp.server.fastmcp import FastMCP
import os
import logging
import json
from typing import Any, List


# Initialize FastMCP server
mcp = FastMCP("context_memory")

# Initialize and run the server
memory_dir = os.environ.get('MEMORY_PATH', "./temp")
    
memory = {}
logger = logging.getLogger(__name__)

# create mcp tool that create or update new entry to the memory 
@mcp.tool()
async def add_or_update_memory_entry(entry_key: str, value: Any) -> bool:
    """Adds a new memory entry or updates an existing one.

    Args:
        entry_key: the key of the memory entry.
        value: the value of the memory entry. This value should be the output of serializing the actual value, so if the value is an object, so it should be serialized by transforming it to JSON object.
    """
    memory[entry_key] = value
    with open(os.path.join(memory_dir, entry_key), "w") as f:
        f.write(json.dumps(value))
    return True

# create mcp tool that create or update new entry to the memory 
@mcp.tool()
async def add_or_update_memory_entries(entries_dict: dict[str, Any]) -> bool:
    """Adds a new memory entries or updates the existing ones. 
    Use this tool if you want to add or update multiple entries at once.

    Args:
        entries_dict: the dictionary of the memory entries. The key is the entry key and the value is the entry value. The value should be the output of serializing the actual value, so if the value is an object, so it should be serialized by transforming it to JSON object.
    """
    for entry_key, value in entries_dict.items():
        memory[entry_key] = value
        with open(os.path.join(memory_dir, entry_key), "w") as f:
            f.write(json.dumps(value))
    return True


# create mcp tool that read the memory
@mcp.tool()
async def read_memory_entry(entry_key: str) -> Any:
    """Reads the memory entry. The returned value can be desrialized from JSON object to the actual object if needed

    Args:
        entry_key: the key of the memory entry.
    """
    if entry_key in memory:
        return memory[entry_key]
    else:
        if os.path.exists(os.path.join(memory_dir, entry_key)):
            with open(os.path.join(memory_dir, entry_key), "r") as f:
                memory[entry_key] = json.loads(f.read())
                return memory[entry_key]
        else:
            return "No entry found for the given key."
        

# create mcp tool that read the memory
@mcp.tool()
async def read_memory_entries(entry_key: List[str]) -> dict[str, Any]:
    """Reads the memory entries. The returned value can be desrialized from JSON object to the actual object if needed.
    Use this tool if you want to read multiple entries at once.

    Args:
        entry_key: the key of the memory entry.
    """
    result = {}
    for key in entry_key:
        if key in memory:
            result[key] = memory[key]
        else:
            if os.path.exists(os.path.join(memory_dir, key)):
                with open(os.path.join(memory_dir, key), "r") as f:
                    memory[key] = json.loads(f.read())
                    result[key] = memory[key]
            else:
                result[key] = "No entry found for the given key."
    return result
        
# create mcp tool that list all memory entries
@mcp.tool()
async def list_memory_entries() -> List[str]:
    """List all memory entries. The returned value, should be deserialized from JSON Object to the actual list"""
    return list(memory.keys())

# create mcp tool that delete the memory entry
@mcp.tool()
async def delete_memory_entry(entry_key: str) -> bool:
    """Deletes the memory entry.

    Args:
        entry_key: the key of the memory entry.
    """
    if entry_key in memory:
        del memory[entry_key]
    if os.path.exists(os.path.join(memory_dir, entry_key)):
        os.remove(os.path.join(memory_dir, entry_key))
    return True


if __name__ == "__main__":
    if os.path.exists(memory_dir):
        for file in os.listdir(memory_dir):
            with open(os.path.join(memory_dir, file), "r") as f:
                if file.startswith('.'):
                    continue
                memory[file] = json.loads(f.read())
    else:
        os.makedirs(memory_dir, exist_ok=True)  
    mcp.run(transport='stdio')
