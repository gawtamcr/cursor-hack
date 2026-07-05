from setuptools import find_packages, setup


package_name = "raise_core"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", ["launch/core.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="RAISE Demo Team",
    maintainer_email="demo@example.com",
    description="Core ROS 2 nodes for the RAISE troubleshooting demo.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "steering_monitor = raise_core.steering_monitor:main",
            "steering_fault_injector = raise_core.steering_fault_injector:main",
            "lane_follower = raise_core.lane_follower:main",
        ],
    },
)
