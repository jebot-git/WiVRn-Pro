# SPDX-License-Identifier: GPL-3.0-or-later
extends Node3D
## Synthetic OpenXR codec scene. No network, codec settings or session management.
var font := FontFile.new()
const LOOP_SECONDS = 12.0
var desktop := "--desktop" in OS.get_cmdline_user_args()
var take := "unlabelled"
var capture := ""
var fixed_time := -1.0
var elapsed := 0.0
var paused := false
var camera: Camera3D
var rig: Node3D
var motion_material: ShaderMaterial
var moving := Node3D.new()
var status: Sprite3D
var status_elapsed := 0.0
var frame_count := 0
var station := 0
var yaw := 0.0
var pitch := 0.0
var rng := RandomNumberGenerator.new()

func _ready() -> void:
    if font.load_bitmap_font("res://assets/unifont.fnt") != OK:
        push_error("Cannot load bundled bitmap font")
        get_tree().quit(3)
        return
    for arg in OS.get_cmdline_user_args():
        if arg.begins_with("--take="): take = arg.trim_prefix("--take=").left(32)
        if arg.begins_with("--capture="): capture = arg.trim_prefix("--capture=")
        if arg.begins_with("--view="): station = clampi(arg.trim_prefix("--view=").to_int(), 0, 3)
        if arg.begins_with("--fixed-time="):
            fixed_time = arg.trim_prefix("--fixed-time=").to_float()
            paused = true
    rng.seed = 444420
    build_room()
    build_charts()
    build_depth_targets()
    setup_camera()
    if not camera: return
    status = label("", Vector3(0, 2.52, -3.0), 0.0055, Color("80dce8"))
    set_bitmap_text(status, take + " | repeatable 12s loop")
    label("SPACE / TRIGGER: PAUSE     R / B OR Y: REPEAT     0..3: DESKTOP VIEWS", Vector3(0, 0.7, -3), 0.0045)
    label("12 SECOND LOOP  /  FIXED SEED 444420  /  NO AA  /  BITMAP TYPE", Vector3(0, 0.58, -3), 0.0045, Color("9dabb8"))
    update_animation()
    print("CODEC_LAB_READY ", JSON.stringify(metadata()))

func material(color: Color) -> StandardMaterial3D:
    var m := StandardMaterial3D.new()
    m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
    m.albedo_color = color
    m.cull_mode = BaseMaterial3D.CULL_DISABLED
    return m

func mesh(mesh_resource: Mesh, pos: Vector3, mat: Material, parent: Node3D = self) -> MeshInstance3D:
    var node := MeshInstance3D.new()
    node.mesh = mesh_resource
    node.position = pos
    node.material_override = mat
    parent.add_child(node)
    return node

func box(pos: Vector3, size: Vector3, color: Color, parent: Node3D = self) -> MeshInstance3D:
    var shape := BoxMesh.new()
    shape.size = size
    return mesh(shape, pos, material(color), parent)

func set_bitmap_text(node: Sprite3D, value: String) -> void:
    # Direct bitmap layout avoids shaping/rendering through vector-font fallbacks.
    if node.get_meta("bitmap_text", "") == value and node.texture: return
    node.set_meta("bitmap_text", value)
    var rows := value.split("\n")
    var width := 1
    for row in rows:
        var row_width := 0
        for i in range(row.length()):
            var code := row.unicode_at(i) if font.has_char(row.unicode_at(i)) else 63
            row_width += int(font.get_glyph_advance(0, 16, code).x)
        width = maxi(width, row_width)
    var image := Image.create(width, maxi(1, rows.size() * 18), false, Image.FORMAT_RGBA8)
    image.fill(Color.TRANSPARENT)
    var atlas := font.get_texture_image(0, Vector2i(16, 0), 0)
    for line in range(rows.size()):
        var x := 0
        for i in range(rows[line].length()):
            var code := rows[line].unicode_at(i) if font.has_char(rows[line].unicode_at(i)) else 63
            var rect := Rect2i(font.get_glyph_uv_rect(0, Vector2i(16, 0), code))
            var offset := Vector2i(font.get_glyph_offset(0, Vector2i(16, 0), code))
            image.blit_rect(atlas, rect, Vector2i(x, line * 18 + 14) + offset)
            x += int(font.get_glyph_advance(0, 16, code).x)
    if node.texture and Vector2i(node.texture.get_size()) == image.get_size():
        (node.texture as ImageTexture).update(image)
    else:
        node.texture = ImageTexture.create_from_image(image)

func label(value: String, pos: Vector3, pixel_size := 0.002, color := Color("e1ebf4")) -> Sprite3D:
    var node := Sprite3D.new()
    node.pixel_size = pixel_size
    node.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
    node.alpha_cut = SpriteBase3D.ALPHA_CUT_DISCARD
    node.modulate = color
    node.position = pos
    set_bitmap_text(node, value)
    add_child(node)
    return node

func quad(pos: Vector3, mat: Material, size := Vector2(1.38, 1.035)) -> MeshInstance3D:
    var shape := QuadMesh.new()
    shape.size = size
    return mesh(shape, pos, mat)

func build_room() -> void:
    var world := WorldEnvironment.new()
    var env := Environment.new()
    env.background_mode = Environment.BG_COLOR
    env.background_color = Color("09121c")
    env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
    world.environment = env
    add_child(world)
    box(Vector3(0, -0.06, -2), Vector3(12, 0.1, 12), Color("17232d"))
    for i in range(-6, 7):
        box(Vector3(i, 0.001, -2), Vector3(0.008, 0.003, 12), Color("3c505c"))
        box(Vector3(0, 0.001, i - 2), Vector3(12, 0.003, 0.008), Color("3c505c"))
    box(Vector3(0, 0.005, 0), Vector3(0.6, 0.004, 0.008), Color("80dce8"))
    box(Vector3(0, 0.005, 0), Vector3(0.008, 0.004, 0.6), Color("80dce8"))
    for x in [-2.3, 2.3]:
        box(Vector3(x, 1.5, -3.12), Vector3(0.018, 3, 0.018), Color("80dce8"))
    label("WiVRn / CODEC LAB", Vector3(0, 2.83, -3.0), 0.009)
    # Static seeded high-detail rods stress peripheral compression without a heavy renderer.
    var instances := MultiMesh.new()
    instances.transform_format = MultiMesh.TRANSFORM_3D
    instances.use_colors = true
    var rod := BoxMesh.new()
    rod.size = Vector3(0.018, 0.9, 0.018)
    instances.mesh = rod
    instances.instance_count = 480
    for i in range(480):
        var x := rng.randf_range(-5.0, 5.0)
        if absf(x) < 2.4: x += 2.5 * signf(x)
        var transform := Transform3D(Basis.IDENTITY, Vector3(x, 0.7, rng.randf_range(-7.0, -3.6)))
        transform.basis = transform.basis.scaled(Vector3(1, rng.randf_range(0.2, 2), 1))
        instances.set_instance_transform(i, transform)
        instances.set_instance_color(i, Color.from_hsv(rng.randf(), 0.7, 0.65))
    var rods := MultiMeshInstance3D.new()
    rods.multimesh = instances
    var rod_material := material(Color.WHITE)
    rod_material.vertex_color_use_as_albedo = true
    rods.material_override = rod_material
    add_child(rods)

func build_charts() -> void:
    for x in [-1.47, 0.0, 1.47]:
        box(Vector3(x, 1.7, -3.035), Vector3(1.42, 1.075, 0.035), Color("344652"))
        box(Vector3(x, 1.02, -3.025), Vector3(1.42, 0.21, 0.025), Color("0c151f"))
    box(Vector3(0, 0.64, -3.025), Vector3(4.36, 0.24, 0.025), Color("0c151f"))
    var chart := material(Color.WHITE)
    chart.albedo_texture = ImageTexture.create_from_image(Image.load_from_file("res://assets/chroma.png"))
    chart.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
    quad(Vector3(-1.47, 1.7, -3), chart)
    motion_material = ShaderMaterial.new()
    motion_material.shader = preload("res://motion.gdshader")
    quad(Vector3(0, 1.7, -3), motion_material)
    var ramps := ShaderMaterial.new()
    ramps.shader = preload("res://ramps.gdshader")
    quad(Vector3(1.47, 1.7, -3), ramps)
    label("01 / CHROMA + TYPE", Vector3(-1.47, 2.34, -3), 0.0065)
    label("02 / MOTION + EDGES", Vector3(0, 2.34, -3), 0.0065)
    label("03 / FLOAT GRADIENTS", Vector3(1.47, 2.34, -3), 0.0065)
    label("1 / 2 / 4 / 8 SOURCE TEXELS\nY-matched stripes; Unicode type", Vector3(-1.47, 1.02, -3), 0.0045)
    label("80 texels/s; 32-spoke wheel\nWatch crawl, ringing and smearing", Vector3(0, 1.02, -3), 0.0045)
    label("WHITE / NEAR-BLACK / R / G / B\nLast row: narrow mid-grey range", Vector3(1.47, 1.02, -3), 0.0045)

func build_depth_targets() -> void:
    add_child(moving)
    for i in range(24):
        var color := Color.from_hsv(float(i) / 24.0, 0.9, 0.9)
        var fin := box(Vector3.ZERO, Vector3(0.008, 0.42, 0.055), color, moving)
        fin.set_meta("index", i)
    for side in [-1, 1]:
        var sphere := SphereMesh.new()
        sphere.radius = 0.13
        sphere.height = 0.26
        sphere.radial_segments = 24
        sphere.rings = 12
        mesh(sphere, Vector3(side * 0.5, 0.45, -1.3), material(Color("e0eaf2")), moving)
        for distance in [1.0, 2.0, 4.0]:
            var x: float = side * (1.6 + distance * 0.25)
            box(Vector3(x, 0.8, -distance), Vector3(0.018, 0.9, 0.018), Color("dfeaf3"))
            label("%.0f m" % distance, Vector3(x, 1.35, -distance), 0.0025, Color("80dce8"))

func setup_camera() -> void:
    if desktop:
        rig = Node3D.new()
        rig.position = Vector3(0, 1.65, 0.5)
        add_child(rig)
        camera = Camera3D.new()
        camera.fov = 75
        rig.add_child(camera)
        set_station(station)
    else:
        var xr := XRServer.find_interface("OpenXR")
        if not xr or not xr.is_initialized():
            push_error("OpenXR unavailable. Start a WiVRn session, use run.sh --xr, or use --desktop to preview.")
            get_tree().quit(2)
            return
        get_viewport().use_xr = true
        rig = XROrigin3D.new()
        add_child(rig)
        camera = XRCamera3D.new()
        rig.add_child(camera)
        for hand in ["left_hand", "right_hand"]:
            var controller := XRController3D.new()
            controller.tracker = hand
            controller.pose = "aim"
            rig.add_child(controller)
            controller.button_pressed.connect(controller_button)
            box(Vector3(0, 0, -0.08), Vector3(0.006, 0.006, 0.16), Color("80dce8"), controller)
    camera.current = true
    camera.near = 0.05
    camera.far = 30

func controller_button(action: String) -> void:
    if action == "select": toggle_pause()
    if action == "reset": repeat_loop()

func toggle_pause() -> void:
    if fixed_time >= 0:
        elapsed = fposmod(fixed_time, LOOP_SECONDS)
        fixed_time = -1.0
    paused = not paused

func set_station(index: int) -> void:
    station = index
    yaw = 0.0
    pitch = 0.0
    rig.rotation = Vector3.ZERO
    rig.position = Vector3(0, 1.65, 0.5) if station == 0 else Vector3((station - 2) * 1.47, 1.7, -1.25)

func repeat_loop() -> void:
    elapsed = 0.0
    fixed_time = -1.0
    paused = false
    update_animation()

func _unhandled_input(event: InputEvent) -> void:
    if event is InputEventKey and event.pressed and not event.echo:
        if event.keycode == KEY_W and event.ctrl_pressed:
            get_tree().quit()
        elif event.keycode == KEY_SPACE: toggle_pause()
        elif event.keycode == KEY_R: repeat_loop()
        elif event.keycode == KEY_ESCAPE: Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
        elif desktop and event.keycode >= KEY_0 and event.keycode <= KEY_3:
            set_station(event.keycode - KEY_0)
    if desktop and event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_RIGHT:
        Input.mouse_mode = Input.MOUSE_MODE_CAPTURED if event.pressed else Input.MOUSE_MODE_VISIBLE
    if desktop and event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
        yaw -= event.relative.x * 0.003
        pitch = clampf(pitch - event.relative.y * 0.003, -1.2, 1.2)
        rig.rotation = Vector3(pitch, yaw, 0)

func _process(delta: float) -> void:
    if not camera: return
    if not paused: elapsed = fmod(elapsed + delta, LOOP_SECONDS)
    if desktop and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
        var direction := Vector3.ZERO
        if Input.is_physical_key_pressed(KEY_W): direction.z -= 1
        if Input.is_physical_key_pressed(KEY_S): direction.z += 1
        if Input.is_physical_key_pressed(KEY_A): direction.x -= 1
        if Input.is_physical_key_pressed(KEY_D): direction.x += 1
        rig.position += rig.basis * direction.normalized() * delta
    update_animation()
    status_elapsed += delta
    if status_elapsed >= 0.2:
        status_elapsed = 0.0
        set_bitmap_text(status, "%s   |   t=%05.2fs / 12s   |   %s" % [take, phase(), "PAUSED" if paused else "PLAY"])
    frame_count += 1
    if not capture.is_empty() and frame_count == 12:
        save_capture.call_deferred()

func phase() -> float:
    return fposmod(fixed_time if fixed_time >= 0 else elapsed, LOOP_SECONDS)

func update_animation() -> void:
    var time := phase()
    motion_material.set_shader_parameter("phase", time)
    for i in range(24):
        var angle := float(i) * TAU / 24.0 + time * TAU / LOOP_SECONDS
        var fin: Node3D = moving.get_child(i)
        fin.position = Vector3(sin(angle) * 0.38, 0.65, -1.7 + cos(angle) * 0.38)
        fin.rotation.y = angle
    for i in range(2):
        var sphere: Node3D = moving.get_child(24 + i)
        sphere.position.x = (1 if i == 0 else -1) * (0.6 + sin(time * TAU / LOOP_SECONDS) * 0.28)

func metadata() -> Dictionary:
    return {"scene": "codec-lab-v1", "seed": 444420, "loop_seconds": LOOP_SECONDS,
        "phase_seconds": phase(), "paused": paused, "take_label": take,
        "desktop_view": station, "viewport_size": [get_viewport().size.x, get_viewport().size.y],
        "mode": "desktop" if desktop else "openxr", "godot": Engine.get_version_info().string,
        "note": "Take label is manual. Source captures are not decoded headset frames."}

func save_capture() -> void:
    await RenderingServer.frame_post_draw
    var image := get_viewport().get_texture().get_image()
    var result := image.save_png(capture)
    var details := metadata()
    details["captured_size"] = [image.get_width(), image.get_height()]
    var file := FileAccess.open(capture + ".json", FileAccess.WRITE)
    if file: file.store_string(JSON.stringify(details, "  ") + "\n")
    print("CODEC_LAB_CAPTURE ", capture, " result=", result)
    get_tree().quit(0 if result == OK else 3)
