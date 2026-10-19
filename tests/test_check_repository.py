import unittest
from scripts.check_repository import LIMIT, violation


class UploadPolicyTests(unittest.TestCase):
    def test_size_boundary(self):
        self.assertIsNone(violation("src/app.py", LIMIT - 1))
        self.assertIsNotNone(violation("src/app.py", LIMIT))

    def test_data_and_credentials(self):
        for name in ("data/sample.json", "nested/data/weights.pt", "datasets/sample.npy", "dump.sqlite", ".env", ".env.production", "backup.zip"):
            with self.subTest(name=name):
                self.assertIsNotNone(violation(name, 1))

    def test_code_docs_and_example_config(self):
        for name in ("src/app.py", "README.md", ".env.example", ".env.production.example", "config/app.yaml"):
            with self.subTest(name=name):
                self.assertIsNone(violation(name, 1))

    def test_models_allowed_below_size_limit(self):
        for name in ("nested/models/config.json", "checkpoints/weights.PT", "models/weights.pth", "models/model.onnx", "models/model.safetensors", "models/weights.h5", "models/model.bin", "models/model.pkl", "models/model.pickle", "models/weights.npy", "models/weights.npz"):
            with self.subTest(name=name):
                self.assertIsNone(violation(name, LIMIT - 1))
                self.assertIsNotNone(violation(name, LIMIT))


if __name__ == "__main__":
    unittest.main()
