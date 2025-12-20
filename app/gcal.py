import os

from aiohttp import ClientSession


async def get_gcals():
    if url := os.getenv("GCAL_URL"):
        async with ClientSession() as session:
            async with session.get(url) as response:
                if response.ok:
                    return await response.json()
    return {}
