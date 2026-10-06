# SPDX-License-Identifier: GPL-3.0-or-later
extends XROrigin3D
var camera: Camera3D
var left: XRController3D
var right: XRController3D
var xr_active = false
var snap_ready = true
var was_reset = false
func _ready() -> void:
	var xr = XRServer.find_interface("OpenXR")
	xr_active = xr != null and xr.is_initialized()
	camera = XRCamera3D.new() if xr_active else Camera3D.new()
	camera.name = "Camera"
	add_child(camera)
	if xr_active:
		get_viewport().physics_object_picking = false
		get_viewport().use_xr = true
		DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	else:
		camera.position.y = 1.65
	camera.current = true
	var listener = AudioListener3D.new()
	camera.add_child(listener)
	listener.make_current()
	left = XRController3D.new()
	left.name = "LeftController"
	left.tracker = &"left_hand"
	left.pose = &"grip"
	add_child(left)
	right = XRController3D.new()
	right.name = "RightController"
	right.tracker = &"right_hand"
	right.pose = &"grip"
	add_child(right)
	for hand in [left,right]:
		var mesh = BoxMesh.new()
		mesh.size = Vector3(0.04,0.04,0.1)
		var model = MeshInstance3D.new()
		model.mesh = mesh
		hand.add_child(model)
func face_speakers() -> void:
	rotate_y(-camera.global_rotation.y)
	move_head_to(Vector3(0,0,1.8))
func _process(delta: float) -> void:
	var stick = Vector2.ZERO
	if xr_active and left.get_is_active():
		stick = left.get_vector2("primary")
	else:
		stick = Vector2(float(Input.is_physical_key_pressed(KEY_D))-float(Input.is_physical_key_pressed(KEY_A)),float(Input.is_physical_key_pressed(KEY_W))-float(Input.is_physical_key_pressed(KEY_S)))
	if stick.length() > 0.15:
		var forward = -camera.global_basis.z
		forward.y = 0
		forward = forward.normalized()
		position += (forward.cross(Vector3.UP)*stick.x+forward*stick.y)*delta*1.5
		position.x = clampf(position.x,-4.3,4.3)
		position.z = clampf(position.z,-4.3,4.3)
	if xr_active and right.get_is_active():
		var reset = right.is_button_pressed("by_button")
		if reset and not was_reset:
			face_speakers()
		was_reset = reset
		var turn = right.get_vector2("primary").x
		if absf(turn)<0.3:
			snap_ready = true
		if absf(turn)>0.7 and snap_ready:
			var before = camera.global_position
			rotate_y(-signf(turn)*PI/6)
			position += before-camera.global_position
			snap_ready = false
func _unhandled_input(event: InputEvent) -> void:
	if not xr_active and event is InputEventMouseMotion and Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT):
		rotate_y(-event.relative.x*0.003)
		camera.rotation.x = clampf(camera.rotation.x-event.relative.y*0.003,-1.2,1.2)
func move_head_to(location: Vector3) -> void:
	var offset = camera.global_position-global_position
	global_position = location-Vector3(offset.x,0,offset.z)
