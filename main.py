import asyncio
from mega_rotate import rotate_mega_link
from patreon_update import update_patreon_link


async def main():
    new_link = rotate_mega_link()   # This is sync – keep as is
    print(f"New MEGA link: {new_link}")

    await update_patreon_link(new_link)   # Now async
    print("Patreon post updated.")


if __name__ == "__main__":
    asyncio.run(main())
