from setuptools import setup

setup(
    name="mcritweb",
    version="1.5.0",
    packages=["mcritweb"],
    # inherited, not intrinsic: nothing in mcritweb's own source needs 3.12, but
    # mcrit has declared ">=3.12" since v1.10.0 and the pin below is >=1.10.0, so
    # no satisfiable mcrit exists any lower. Undeclared, an install on 3.10 fails
    # with a resolver error about mcrit that names neither Python nor the version
    # the reader needs.
    python_requires=">=3.12",
    include_package_data=True,
    install_requires=[
        "flask>=3.0",
        "werkzeug>=3.0",
        "flask-dropzone",
        "Pillow",
        "numpy",
        "scipy", 
        "fastcluster",
        "networkx",
        "mcrit>=1.10.0",
        "levenshtein",
        "markdown"
    ],
)
