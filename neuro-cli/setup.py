from setuptools import find_packages, setup

with open("README.md") as f:
    readme = f.read()


setup(
    name="neuro-cli",
    version="21.1.13",
    python_requires=">=3.6.0",
    # Make sure to pin versions of install_requires
    install_requires=[
        "aiohttp>=3.7.2",
        "yarl>=1.6.2",
        'dataclasses>=0.7; python_version<"3.7"',
        "pyyaml>=3.0",
        'async-generator>=1.5; python_version<"3.7"',
        'async-exit-stack>=1.0.1; python_version<"3.7"',
        "python-jose>=3.0.0",
        "python-dateutil>=2.7.0",
        "aiodocker>=0.18.7",
        "neuro-sdk>=21.1.13",
        "click>=7.0",
        "humanize>=0.5",
        "typing_extensions>=3.7.4",
        # certifi has no version requirement
        # CLI raises a warning for outdated package instead
        "certifi",
        "cookiecutter>=0.9.0",
        "atomicwrites>=1.0",
        "wcwidth>=0.1.7",
        "toml>=0.10.0",
        "prompt-toolkit>=3.0.13",
        "rich>=9.1.0",
    ],
    include_package_data=True,
    description="Neuro Platform API client",
    long_description=readme,
    long_description_content_type="text/markdown; charset=UTF-8; variant=GFM",
    author="Neu.ro Team",
    author_email="team@neu.ro",
    license="Apache License, version 2.0",
    url="https://neuro.ro/",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={
        "console_scripts": [
            "neuro=neuro_cli.main:main",
            "docker-credential-neuro=neuro_cli.docker_credential_helper:main",
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Intended Audience :: Information Technology",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development",
        "Topic :: Utilities",
        "License :: OSI Approved :: Apache Software License",
    ],
)
