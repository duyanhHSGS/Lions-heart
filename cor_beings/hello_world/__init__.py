"""Hello World Cor Leonis hand."""

from cor_being import Being, Life, World


class HelloWorld(Being):
    """Shout a greeting when the hand starts."""

    name = "hello_world"

    def birth(self, world: World, life: Life) -> None:
        # TODO: Replace the demo greeting with the hand's real work when this plugin grows.
        print("hello world")


__all__ = ["HelloWorld"]
