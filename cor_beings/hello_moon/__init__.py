"""Hello Moon Cor Leonis hand."""

from cor_being import Being, Life, World


class HelloMoon(Being):
    """Shout a greeting when the hand starts."""

    name = "hello_moon"

    def birth(self, world: World, life: Life) -> None:
        # TODO: Replace the demo greeting with the hand's real work when this plugin grows.
        print("hello moon")


__all__ = ["HelloMoon"]
