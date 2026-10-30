#!/usr/bin/env python3
"""Generate the SITL Gazebo flight park: terrain, textures and world SDF.

Everything is produced locally — no Fuel downloads and no imported meshes. The
design target is integrated graphics, so realism comes from lighting, a sky, a
textured heightmap and plenty of scale references, rather than from polygon
count. Every prop is a primitive and every prop is static, which keeps both the
draw calls and the physics broadphase cheap.

Re-run after editing to regenerate; output is deterministic for a fixed seed.
"""

import pathlib
import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent / "worlds"
TERRAIN = ROOT / "models" / "terrain"
TEX = TERRAIN / "materials" / "textures"
RNG = np.random.default_rng(7)

HEIGHT_N = 257          # heightmaps must be 2^n + 1
TEX_N = 512             # small enough to stay comfortably in iGPU memory
TERRAIN_M = 240.0       # metres across
RELIEF_M = 7.0          # metres from lowest to highest point


# ----------------------------------------------------------------- textures

def fbm(shape, octaves, persistence=0.5):
    """Fractal value noise that wraps cleanly, so textures tile without seams."""
    total = np.zeros(shape, dtype=np.float64)
    amplitude, norm = 1.0, 0.0
    for octave in range(octaves):
        freq = 2 ** (octave + 1)
        coarse = RNG.random((freq, freq))
        ys = np.linspace(0, freq, shape[0], endpoint=False)
        xs = np.linspace(0, freq, shape[1], endpoint=False)
        y0, x0 = np.floor(ys).astype(int) % freq, np.floor(xs).astype(int) % freq
        y1, x1 = (y0 + 1) % freq, (x0 + 1) % freq
        fy, fx = (ys - np.floor(ys))[:, None], (xs - np.floor(xs))[None, :]
        fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
        top = coarse[np.ix_(y0, x0)] * (1 - fx) + coarse[np.ix_(y0, x1)] * fx
        bot = coarse[np.ix_(y1, x0)] * (1 - fx) + coarse[np.ix_(y1, x1)] * fx
        total += (top * (1 - fy) + bot * fy) * amplitude
        norm += amplitude
        amplitude *= persistence
    return total / norm


def ground_texture(name, base, speck, speck_strength=0.16, octaves=6):
    n = fbm((TEX_N, TEX_N), octaves=octaves)
    n = (n - n.min()) / (np.ptp(n) + 1e-9)
    grain = RNG.random((TEX_N, TEX_N))
    rgb = np.zeros((TEX_N, TEX_N, 3))
    for i in range(3):
        rgb[..., i] = base[i] * (0.80 + 0.40 * n) + speck[i] * speck_strength * grain
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").save(TEX / name)


def flat_normal():
    """A neutral normal map. The heightmap texture slots require one, and a flat
    map costs nothing while keeping the shader path identical for all layers."""
    a = np.zeros((8, 8, 3), dtype=np.uint8)
    a[..., 0], a[..., 1], a[..., 2] = 128, 128, 255
    Image.fromarray(a, "RGB").save(TEX / "flat_normal.png")


def heightmap():
    h = fbm((HEIGHT_N, HEIGHT_N), octaves=5, persistence=0.55)
    h = (h - h.min()) / (np.ptp(h) + 1e-9)

    # Flatten the middle so the quad spawns and lands level, fading back into
    # the hills so the collision surface has no step in it.
    yy, xx = np.mgrid[0:HEIGHT_N, 0:HEIGHT_N]
    c = (HEIGHT_N - 1) / 2
    r = np.hypot(yy - c, xx - c) / c
    pad = np.clip((r - 0.16) / 0.30, 0.0, 1.0)
    h = h * (pad * pad * (3 - 2 * pad))

    Image.fromarray((h * 255).astype(np.uint8), "L").save(TEX / "terrain.png")
    return h


# --------------------------------------------------------------------- SDF

def box(name, pose, size, rgb, collide=True, shadows=False):
    col = f"""
        <collision name="c"><geometry><box><size>{size}</size></box></geometry></collision>""" if collide else ""
    return f"""
    <model name="{name}">
      <static>true</static>
      <pose>{pose}</pose>
      <link name="l">{col}
        <visual name="v">
          <cast_shadows>{'true' if shadows else 'false'}</cast_shadows>
          <geometry><box><size>{size}</size></box></geometry>
          <material><ambient>{rgb} 1</ambient><diffuse>{rgb} 1</diffuse>
            <specular>0.1 0.1 0.1 1</specular></material>
        </visual>
      </link>
    </model>"""


def cylinder(name, pose, radius, length, rgb, collide=True, shadows=False):
    geom = f"<cylinder><radius>{radius}</radius><length>{length}</length></cylinder>"
    col = f"""
        <collision name="c"><geometry>{geom}</geometry></collision>""" if collide else ""
    return f"""
    <model name="{name}">
      <static>true</static>
      <pose>{pose}</pose>
      <link name="l">{col}
        <visual name="v">
          <cast_shadows>{'true' if shadows else 'false'}</cast_shadows>
          <geometry>{geom}</geometry>
          <material><ambient>{rgb} 1</ambient><diffuse>{rgb} 1</diffuse>
            <specular>0.1 0.1 0.1 1</specular></material>
        </visual>
      </link>
    </model>"""


def sphere(name, pose, radius, rgb, collide=True, shadows=False):
    geom = f"<sphere><radius>{radius}</radius></sphere>"
    col = f"""
        <collision name="c"><geometry>{geom}</geometry></collision>""" if collide else ""
    return f"""
    <model name="{name}">
      <static>true</static>
      <pose>{pose}</pose>
      <link name="l">{col}
        <visual name="v">
          <cast_shadows>{'true' if shadows else 'false'}</cast_shadows>
          <geometry>{geom}</geometry>
          <material><ambient>{rgb} 1</ambient><diffuse>{rgb} 1</diffuse>
            <specular>0.05 0.05 0.05 1</specular></material>
        </visual>
      </link>
    </model>"""


def gate(i, x, y, yaw):
    """A race gate: two uprights and a top bar. Three primitives, one material."""
    w, h, t = 1.6, 2.2, 0.09
    o = f"{x} {y} 0 0 0 {yaw:.3f}"
    parts = [
        cylinder(f"gate{i}_l", f"{x - w/2*np.cos(yaw+np.pi/2):.2f} "
                               f"{y - w/2*np.sin(yaw+np.pi/2):.2f} {h/2} 0 0 0",
                 t, h, "0.85 0.25 0.12"),
        cylinder(f"gate{i}_r", f"{x + w/2*np.cos(yaw+np.pi/2):.2f} "
                               f"{y + w/2*np.sin(yaw+np.pi/2):.2f} {h/2} 0 0 0",
                 t, h, "0.85 0.25 0.12"),
        box(f"gate{i}_top", f"{x} {y} {h} 0 0 {yaw:.3f}",
            f"{t*2} {w + t*2} {t*2}", "0.95 0.95 0.95"),
    ]
    return "".join(parts)


def tree(i, x, y, scale):
    trunk_h = 3.0 * scale
    return (cylinder(f"tree{i}_t", f"{x:.2f} {y:.2f} {trunk_h/2:.2f} 0 0 0",
                     0.18 * scale, trunk_h, "0.30 0.22 0.14", shadows=True)
            + sphere(f"tree{i}_c", f"{x:.2f} {y:.2f} {trunk_h + 1.1*scale:.2f} 0 0 0",
                     1.6 * scale, "0.18 0.33 0.14", shadows=True))


def build_world():
    parts = []

    # A short course of gates through the flat centre, then out over the hills.
    course = [(0, 12, 0.0), (0, 26, 0.0), (12, 38, 0.9), (28, 40, 1.57),
              (40, 28, 2.2), (38, 12, 3.14)]
    for i, (x, y, yaw) in enumerate(course):
        parts.append(gate(i, x, y, yaw))

    # Pylons marking the landing area — the closest, highest-contrast speed cue.
    for i in range(8):
        a = i * np.pi / 4
        parts.append(cylinder(f"pylon{i}", f"{8*np.cos(a):.2f} {8*np.sin(a):.2f} 0.35 0 0 0",
                              0.16, 0.7, "0.95 0.55 0.05"))

    # Containers: large, flat-sided landmarks that read from a long way off.
    for i, (x, y, yaw, rgb) in enumerate([
            (-26, 18, 0.3, "0.20 0.38 0.55"), (-30, 6, 0.0, "0.55 0.22 0.20"),
            (-22, -14, 1.1, "0.42 0.44 0.20"), (18, -24, 0.6, "0.30 0.34 0.38")]):
        parts.append(box(f"container{i}", f"{x} {y} 1.3 0 0 {yaw}",
                         "6.0 2.4 2.6", rgb, shadows=True))

    # Trees scattered outside the flying box, for parallax and depth.
    for i in range(14):
        a = RNG.uniform(0, 2 * np.pi)
        d = RNG.uniform(45, 95)
        parts.append(tree(i, d * np.cos(a), d * np.sin(a), RNG.uniform(0.8, 1.5)))

    # Landing pad at the origin, so the takeoff point is unambiguous.
    parts.append(cylinder("pad", "0 0 0.02 0 0 0", 2.0, 0.04,
                          "0.16 0.16 0.18", collide=False))

    props = "".join(parts)

    return f"""<?xml version="1.0" ?>
<!-- Generated by test_tools/make_world_assets.py — edit that, not this. -->
<sdf version="1.9">
  <world name="flight_park">

    <physics name="400hz" type="ignored">
      <max_step_size>0.0025</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>

    <plugin filename="gz-sim-physics-system"
            name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-user-commands-system"
            name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system"
            name="gz::sim::systems::SceneBroadcaster"/>
    <plugin filename="gz-sim-sensors-system"
            name="gz::sim::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>
    <plugin filename="gz-sim-imu-system"
            name="gz::sim::systems::Imu"/>

    <!-- Sky and haze do most of the realism work and cost almost nothing.
         The grid is off because it reads as a debug view, not a place. -->
    <scene>
      <ambient>0.45 0.47 0.52 1</ambient>
      <background>0.62 0.72 0.85 1</background>
      <sky/>
      <shadows>true</shadows>
      <grid>false</grid>
    </scene>

    <!-- One shadow-casting light. A second would roughly double shadow cost. -->
    <light type="directional" name="sun">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 40 0 0 0</pose>
      <diffuse>0.95 0.92 0.86 1</diffuse>
      <specular>0.25 0.25 0.25 1</specular>
      <direction>-0.4 0.25 -0.86</direction>
    </light>

    <include>
      <uri>model://terrain</uri>
    </include>
{props}

    <include>
      <uri>model://test_quad</uri>
      <pose>0 0 0.5 0 0 0</pose>
    </include>

  </world>
</sdf>
"""


def terrain_model():
    layers = ""
    for tex, size in (("grass", 10), ("dirt", 14), ("rock", 18)):
        layers += f"""
              <texture>
                <diffuse>model://terrain/materials/textures/{tex}.png</diffuse>
                <normal>model://terrain/materials/textures/flat_normal.png</normal>
                <size>{size}</size>
              </texture>"""
    geom = f"""
            <heightmap>
              <uri>model://terrain/materials/textures/terrain.png</uri>
              <size>{TERRAIN_M} {TERRAIN_M} {RELIEF_M}</size>
              <pos>0 0 0</pos>{layers}
              <blend><min_height>1.6</min_height><fade_dist>1.6</fade_dist></blend>
              <blend><min_height>4.0</min_height><fade_dist>2.0</fade_dist></blend>
            </heightmap>"""
    return f"""<?xml version="1.0" ?>
<!-- Generated by test_tools/make_world_assets.py — edit that, not this. -->
<sdf version="1.9">
  <model name="terrain">
    <static>true</static>
    <link name="link">
      <collision name="collision">
        <geometry>{geom}
        </geometry>
      </collision>
      <visual name="visual">
        <geometry>{geom}
        </geometry>
      </visual>
    </link>
  </model>
</sdf>
"""


CONFIG = """<?xml version="1.0"?>
<model>
  <name>terrain</name>
  <version>1.0</version>
  <sdf version="1.9">model.sdf</sdf>
  <description>Generated heightmap terrain for the Betaflight SITL flight park.</description>
</model>
"""


if __name__ == "__main__":
    TEX.mkdir(parents=True, exist_ok=True)
    h = heightmap()
    flat_normal()
    ground_texture("grass.png", (74, 96, 48), (150, 170, 90))
    ground_texture("dirt.png", (116, 96, 70), (170, 150, 120))
    ground_texture("rock.png", (104, 104, 102), (160, 160, 158), 0.22, 7)
    (TERRAIN / "model.sdf").write_text(terrain_model())
    (TERRAIN / "model.config").write_text(CONFIG)
    (ROOT / "flight_park.sdf").write_text(build_world())

    total = sum(f.stat().st_size for f in ROOT.rglob("*") if f.is_file())
    print(f"terrain  {HEIGHT_N}x{HEIGHT_N} over {TERRAIN_M:.0f} m, {RELIEF_M:.0f} m relief")
    print(f"world    {ROOT / 'flight_park.sdf'}")
    print(f"assets   {total // 1024} KB total")
