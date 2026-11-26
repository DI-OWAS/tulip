import unittest
import pkgutil
import tulip


class TestImportable(unittest.TestCase):
    @classmethod
    def generate_cases(cls, module):
        """
        Augment this class by test cases for importing all submodules.
        """

        def raise_import(module_name: str):
            raise ImportError(f"Could not import module: '{module_name}'.")

        for submodule in pkgutil.walk_packages(module.__path__,
                                               f"{module.__name__}.",
                                               onerror=raise_import):
            def generate_test(module_name: str):
                def test_func(self: TestImportable):
                    self.assertIsNotNone(__import__(module_name))

                return test_func

            test_method = generate_test(submodule.name)
            test_method.__name__ = f"test_import_{submodule.name}"

            setattr(cls, f"test_import_{submodule.name}", test_method)


TestImportable.generate_cases(tulip)

if __name__ == "__main__":
    unittest.main()
