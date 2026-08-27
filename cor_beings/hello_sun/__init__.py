"""Hello Sun Cor Leonis hand."""

from cor_being import Being, Life, World
from cor_beings.hello_moon import HelloMoon
from cor_beings.hello_world import HelloWorld


class HelloSun(Being):
    """Scream when both hello moon and hello world are available."""

    name = "hello_sun"
    needs = (HelloMoon, HelloWorld)

    def birth(self, world: World, life: Life) -> None:
        print("SON!")


# TODO: Keep this hand dependency-driven; runtime ordering belongs to cor_runtime.
