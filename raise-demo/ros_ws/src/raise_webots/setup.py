from setuptools import setup

package_name = "raise_webots"
data_files = [
    ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
    ("share/" + package_name + "/launch", ["launch/demo.launch.py"]),
    ("share/" + package_name + "/worlds", ["worlds/raise_city.wbt"]),
    ("share/" + package_name + "/resource", ["resource/raise_vehicle.urdf"]),
    ("share/" + package_name, ["package.xml"]),
]

setup(
    name=package_name,
    version="0.2.0",
    packages=[package_name],
    data_files=data_files,
    install_requires=["setuptools", "launch"],
    zip_safe=True,
    maintainer="RAISE Demo Team",
    maintainer_email="demo@example.com",
    description="Webots city simulation for the RAISE steering troubleshooting demo.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "launch.frontend.launch_extension": ["launch_ros = launch_ros"],
    },
)
