#!/usr/bin/env python3
"""Generate a 10-inch quadcopter model for the SITL Gazebo world.

Two things this fixes versus aeroloop's `test_quad`:

  * It looks like a quad — carbon plates, tapered arms, motor bells, two-blade
    254 mm props, a LiPo, a stack, a camera and a VTX antenna — instead of a
    single 470 mm slab with four flat discs. Every part is still a primitive,
    so the whole airframe costs a few dozen draw calls and no mesh files.
  * It can actually fly. `test_quad` has no aerodynamics at all: the Betaflight
    plugin only spins the rotor joints, and without a LiftDrag plugin per blade
    nothing ever generates thrust. This model carries eight, two per rotor.

Sized as a 12S 10" long-range build: 481 mm motor-to-motor, 2.2 kg all-up,
~2.2 kgf per motor at full throttle, so thrust-to-weight lands near 4:1.

Motor numbering, spin directions and the front-right/rear-left CCW pattern are
copied from `test_quad` so the mapping Betaflight's SITL_GAZEBO config expects
(including YAW_MOTORS_REVERSED) still holds.
"""

import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "worlds" / "models" / "quad10"

ARM_XY = 0.17                 # motor offset on each axis -> 481 mm diagonal
PROP_R = 0.127                # 10 inch = 254 mm diameter
MASS_BODY = 2.15              # frame, battery, electronics, motor stators
MASS_PROP = 0.0125
MAX_RAD_S = 838.0             # plugin's "maxRpm" is really rad/s (~8000 rpm)
BLADE_AREA = 0.82             # lumped tuning constant, scaled from the iris
BLADE_CP = 0.085              # centre of pressure, ~2/3 of prop radius
VEL_P_GAIN = 0.05             # rotor velocity PID; iris uses 0.01 for 1/4 the blade area

CARBON = "0.055 0.055 0.062"
METAL = "0.22 0.22 0.25"
PROP_FRONT = "0.86 0.87 0.90"
PROP_REAR = "0.13 0.14 0.17"
BATTERY = "0.09 0.09 0.13"
LABEL = "0.72 0.60 0.10"
LENS = "0.05 0.07 0.10"

# (motor number, x sign, y sign, direction). Preserved from test_quad.
ROTORS = [(0, +1, -1, "ccw"), (1, -1, +1, "ccw"),
          (2, +1, +1, "cw"), (3, -1, -1, "cw")]


def vis(name, pose, geom, rgb, shadows=True):
    return f"""
      <visual name="{name}">
        <pose>{pose}</pose>
        <cast_shadows>{'true' if shadows else 'false'}</cast_shadows>
        <geometry>{geom}</geometry>
        <material><ambient>{rgb} 1</ambient><diffuse>{rgb} 1</diffuse>
          <specular>0.2 0.2 0.2 1</specular></material>
      </visual>"""


def box(sx, sy, sz):
    return f"<box><size>{sx} {sy} {sz}</size></box>"


def cyl(r, l):
    return f"<cylinder><radius>{r}</radius><length>{l}</length></cylinder>"


def airframe():
    v = []
    # plates
    v.append(vis("plate_bottom", "0 0 0 0 0 0", box(0.20, 0.088, 0.004), CARBON))
    v.append(vis("plate_top", "0 0 0.040 0 0 0", box(0.135, 0.088, 0.003), CARBON))
    for i, (sx, sy) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]):
        v.append(vis(f"standoff{i}", f"{0.058*sx} {0.034*sy} 0.021 0 0 0",
                     cyl(0.0035, 0.036), METAL, shadows=False))
    # arms, motor mounts, bells
    for n, sx, sy, _ in ROTORS:
        import math
        yaw = math.atan2(sy * ARM_XY, sx * ARM_XY)
        v.append(vis(f"arm{n}", f"{sx*ARM_XY/2:.4f} {sy*ARM_XY/2:.4f} 0 0 0 {yaw:.4f}",
                     box(0.245, 0.022, 0.0075), CARBON))
        v.append(vis(f"mount{n}", f"{sx*ARM_XY} {sy*ARM_XY} 0.004 0 0 0",
                     cyl(0.0205, 0.007), CARBON, shadows=False))
        v.append(vis(f"bell{n}", f"{sx*ARM_XY} {sy*ARM_XY} 0.018 0 0 0",
                     cyl(0.0175, 0.024), METAL))
    # battery and strap
    v.append(vis("battery", "-0.012 0 0.072 0 0 0", box(0.158, 0.054, 0.050), BATTERY))
    v.append(vis("battery_label", "-0.012 0 0.0975 0 0 0", box(0.090, 0.040, 0.001), LABEL,
                 shadows=False))
    # flight stack
    v.append(vis("stack", "0.004 0 0.0255 0 0 0", box(0.036, 0.036, 0.018), "0.12 0.13 0.15"))
    # camera: body plus a lens tilted up, the way an FPV cam is mounted
    v.append(vis("cam_body", "0.078 0 0.030 0 0 0", box(0.020, 0.021, 0.021), "0.08 0.08 0.09"))
    v.append(vis("cam_lens", "0.090 0 0.034 0 1.134 0", cyl(0.0085, 0.014), LENS))
    # VTX antenna, swept back and up
    v.append(vis("ant_mast", "-0.094 0 0.052 0 -0.52 0", cyl(0.0035, 0.052), "0.10 0.10 0.11",
                 shadows=False))
    v.append(vis("ant_tip", "-0.112 0 0.078 0 -0.52 0", cyl(0.0075, 0.020), "0.80 0.78 0.10",
                 shadows=False))
    return "".join(v)


def rotor(n, sx, sy, direction):
    colour = PROP_FRONT if sx > 0 else PROP_REAR
    x, y = sx * ARM_XY, sy * ARM_XY
    blades = "".join([
        vis("hub", "0 0 0 0 0 0", cyl(0.011, 0.007), METAL, shadows=False),
        # two blades, each pitched a little so they read as a propeller at rest
        vis("blade_a", f"{PROP_R/2:.4f} 0 0 0.18 0 0",
            box(PROP_R, 0.024, 0.0025), colour, shadows=False),
        vis("blade_b", f"{-PROP_R/2:.4f} 0 0 -0.18 0 0",
            box(PROP_R, 0.024, 0.0025), colour, shadows=False),
    ])
    return f"""
    <link name="rotor_{n}">
      <pose relative_to="base_link">{x} {y} 0.034 0 0 0</pose>
      <inertial>
        <mass>{MASS_PROP}</mass>
        <inertia>
          <ixx>1.0e-06</ixx><ixy>0</ixy><ixz>0</ixz>
          <iyy>6.7e-05</iyy><iyz>0</iyz><izz>6.8e-05</izz>
        </inertia>
      </inertial>{blades}
    </link>

    <joint name="rotor_{n}_joint" type="revolute">
      <parent>base_link</parent>
      <child>rotor_{n}</child>
      <axis>
        <xyz>0 0 1</xyz>
        <limit><lower>-1e+16</lower><upper>1e+16</upper></limit>
        <dynamics><damping>0.004</damping></dynamics>
      </axis>
    </joint>"""


def liftdrag(n, direction):
    """Two blade elements per rotor. Without these the rotors spin but the
    aircraft produces no thrust — the bug that grounds aeroloop's test_quad.

    `forward` is the direction the element travels through the air, so it has
    to follow the rotor: an element at +x moves toward +y on a CCW rotor and
    toward -y on a CW one. Give a CW rotor the CCW vectors and it meets the air
    backwards, alpha lands past alpha_stall, and that rotor pushes *down* —
    which tips the aircraft over onto the lifting diagonal as throttle comes in.
    """
    spin = 1 if direction == "ccw" else -1
    out = ""
    for cp_sign in (1, -1):
        cp = f"{cp_sign * BLADE_CP:g} 0 0"
        fwd = f"0 {spin * cp_sign} 0"
        out += f"""
    <plugin name="gz::sim::systems::LiftDrag" filename="gz-sim-lift-drag-system">
      <a0>0.025</a0>
      <alpha_stall>1.4</alpha_stall>
      <cla>0.2500</cla>
      <cda>0.004</cda>
      <cma>0.00</cma>
      <cla_stall>-0.025</cla_stall>
      <cda_stall>0.0</cda_stall>
      <cma_stall>0.0</cma_stall>
      <area>{BLADE_AREA}</area>
      <air_density>1.2041</air_density>
      <cp>{cp}</cp>
      <forward>{fwd}</forward>
      <upward>0 0 1</upward>
      <link_name>rotor_{n}</link_name>
    </plugin>"""
    return out


def imu():
    axes = "".join(f"""
            <{a}><noise type="gaussian"><mean>0.0</mean>
              <stddev>{sd}</stddev></noise></{a}>""" for a, sd in
                   (("x", "2e-4"), ("y", "2e-4"), ("z", "2e-4")))
    acc = "".join(f"""
            <{a}><noise type="gaussian"><mean>0.0</mean>
              <stddev>1.7e-2</stddev></noise></{a}>""" for a in "xyz")
    return f"""
      <sensor name="imu_sensor" type="imu">
        <always_on>1</always_on>
        <update_rate>400</update_rate>
        <imu>
          <angular_velocity>{axes}
          </angular_velocity>
          <linear_acceleration>{acc}
          </linear_acceleration>
        </imu>
      </sensor>"""


def model():
    rotors = "".join(rotor(n, sx, sy, d) for n, sx, sy, d in ROTORS)
    lift = "".join(liftdrag(n, d) for n, _, _, d in ROTORS)
    bfrotors = "".join(f"""
      <rotor id="{n}">
        <jointName>rotor_{n}_joint</jointName>
        <turningDirection>{d}</turningDirection>
        <maxRpm>{MAX_RAD_S}</maxRpm>
        <motorNumber>{n}</motorNumber>
        <rotorVelocitySlowdownSim>1</rotorVelocitySlowdownSim>
        <!-- Without these the rotor PID keeps its default output clamp of
             +/-1.0 (BetaflightPlugin.cc:153) and the props never spin up. -->
        <vel_p_gain>{VEL_P_GAIN}</vel_p_gain>
        <vel_i_gain>0</vel_i_gain>
        <vel_d_gain>0</vel_d_gain>
        <vel_i_max>0</vel_i_max>
        <vel_i_min>0</vel_i_min>
        <vel_cmd_max>{MAX_RAD_S}</vel_cmd_max>
        <vel_cmd_min>-{MAX_RAD_S}</vel_cmd_min>
      </rotor>""" for n, _, _, d in ROTORS)

    return f"""<?xml version="1.0"?>
<!-- Generated by test_tools/make_quad_model.py — edit that, not this. -->
<sdf version="1.9">
  <model name="quad10">

    <link name="base_link">
      <inertial>
        <mass>{MASS_BODY}</mass>
        <inertia>
          <ixx>0.026</ixx><ixy>0</ixy><ixz>0</ixz>
          <iyy>0.026</iyy><iyz>0</iyz><izz>0.050</izz>
        </inertia>
      </inertial>

      <collision name="collision">
        <geometry>{box(0.22, 0.10, 0.075)}</geometry>
      </collision>
{airframe()}
{imu()}
    </link>
{rotors}
{lift}

    <plugin name="gz::sim::systems::BetaflightPlugin" filename="BetaflightPlugin">
      <imuName>imu_sensor</imuName>
      <connectionTimeoutMaxCount>5</connectionTimeoutMaxCount>{bfrotors}
    </plugin>

  </model>
</sdf>
"""


CONFIG = """<?xml version="1.0"?>
<model>
  <name>quad10</name>
  <version>1.0</version>
  <sdf version="1.9">model.sdf</sdf>
  <description>10 inch 12S quadcopter for Betaflight SITL.</description>
</model>
"""

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "model.sdf").write_text(model())
    (OUT / "model.config").write_text(CONFIG)
    diag = 2 * (2 * ARM_XY ** 2) ** 0.5
    total = MASS_BODY + 4 * MASS_PROP
    print(f"quad10: {diag*1000:.0f} mm wheelbase, {PROP_R*2000:.0f} mm props, {total:.2f} kg")
    print(f"        8 LiftDrag blade elements, max {MAX_RAD_S:.0f} rad/s "
          f"({MAX_RAD_S*60/6.2832:.0f} rpm)")
    print(f"        {OUT/'model.sdf'}")
