"""
Setup script for The Count project
"""

from setuptools import setup, find_packages

setup(
    name="the-count",
    version="0.1.0",
    description="Financial account connection and tracking system using Plaid",
    author="The Count Project",
    packages=find_packages(),
    install_requires=[
        "plaid-python==12.0.0",
        "flask==3.0.0",
        "python-dotenv==1.0.0",
    ],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)