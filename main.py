from mega_rotate import rotate_mega_link
from patreon_update import update_patreon_link


def main():
    new_link = rotate_mega_link()
    print(f"New MEGA link: {new_link}")

    update_patreon_link(new_link)
    print("Patreon post updated.")


if __name__ == "__main__":
    main()
