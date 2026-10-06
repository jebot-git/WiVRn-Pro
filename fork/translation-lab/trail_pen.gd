# SPDX-License-Identifier: GPL-3.0-or-later
extends Node3D
const LIFETIME = 45.0
const HOME = Vector3(1.25,1.01,0.95)
var rig: XROrigin3D
var body: Node3D
var tip: Node3D
var particles: CPUParticles3D
var beads: MultiMeshInstance3D
var points: Array[Dictionary] = []
var last_point = Vector3.INF
var held = false
var was_grip = false
var was_clear = false
var desktop_draw = false
func _ready() -> void:
	body = Node3D.new()
	body.name = "GrabbablePen"
	add_child(body)
	body.position = HOME
	var mat = StandardMaterial3D.new()
	mat.albedo_color = Color("#fff0a3")
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	var shaft = CylinderMesh.new()
	shaft.top_radius = 0.013
	shaft.bottom_radius = 0.013
	shaft.height = 0.19
	var model = MeshInstance3D.new()
	model.mesh = shaft
	model.material_override = mat
	body.add_child(model)
	model.rotation.x = PI/2
	tip = Node3D.new()
	body.add_child(tip)
	tip.position.z = -0.11
	var dot = SphereMesh.new()
	dot.radius = 0.006
	dot.height = 0.012
	dot.radial_segments = 6
	dot.rings = 3
	dot.material = mat
	particles = CPUParticles3D.new()
	particles.name = "InkParticleTrail"
	add_child(particles)
	particles.emitting = false
	particles.amount = 2400
	particles.lifetime = LIFETIME
	particles.local_coords = false
	particles.gravity = Vector3.ZERO
	particles.initial_velocity_min = 0
	particles.initial_velocity_max = 0
	particles.spread = 0
	particles.mesh = dot
	# Retained particle beads fill gaps between frames and enable deterministic clearing.
	var mm = MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = dot
	mm.instance_count = 12000
	mm.visible_instance_count = 0
	beads = MultiMeshInstance3D.new()
	beads.name = "HandwritingBeads"
	beads.multimesh = mm
	add_child(beads)
func toggle() -> void:
	if held:
		body.reparent(self,true)
		body.global_position = HOME
		body.rotation = Vector3.ZERO
		held = false
	elif not rig.xr_active or rig.right.global_position.distance_to(body.global_position)<0.4:
		body.reparent(rig.right if rig.xr_active else rig.camera,false)
		body.position = Vector3(0,-0.025,-0.15) if rig.xr_active else Vector3(0.18,-0.12,-0.65)
		body.rotation = Vector3.ZERO
		held = true
	last_point = Vector3.INF
func _process(_delta: float) -> void:
	if rig == null:
		return
	var draw = desktop_draw
	if rig.xr_active:
		var active = rig.right.get_is_active()
		var grip = active and rig.right.get_float("grip")>0.65
		if grip and not was_grip:
			toggle()
		was_grip = grip
		draw = active and rig.right.get_float("trigger")>0.2
		var clear_pressed = rig.left.get_is_active() and rig.left.is_button_pressed("ax_button")
		if clear_pressed and not was_clear:
			clear_ink()
		was_clear = clear_pressed
	elif held:
		var point = rig.camera.project_position(get_viewport().get_mouse_position(),0.8)
		body.global_position = point+rig.camera.global_basis.z*0.11
	particles.global_position = tip.global_position
	particles.emitting = draw and held
	if draw and held:
		write_point(tip.global_position)
	else:
		last_point = Vector3.INF
	var cutoff = Time.get_ticks_msec()-int(LIFETIME*1000)
	var expired = false
	while not points.is_empty() and points[0].time<cutoff:
		points.pop_front()
		expired = true
	if expired:
		_refresh()
func write_point(point: Vector3) -> void:
	var now = Time.get_ticks_msec()
	if last_point.is_finite():
		var distance = point.distance_to(last_point)
		if distance<0.006:
			return
		if distance<=0.3:
			var steps = maxi(1,int(ceil(distance/0.009)))
			for step in range(1,steps+1):
				if points.size()<12000:
					points.append({"position":last_point.lerp(point,float(step)/steps),"time":now})
	else:
		if points.size()<12000:
			points.append({"position":point,"time":now})
	last_point = point
	_refresh()
func _refresh() -> void:
	beads.multimesh.visible_instance_count = points.size()
	for i in range(points.size()):
		beads.multimesh.set_instance_transform(i,Transform3D(Basis.IDENTITY,points[i].position))
func clear_ink() -> void:
	points.clear()
	last_point = Vector3.INF
	particles.restart()
	particles.emitting = false
	_refresh()
func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode==KEY_TAB:
			toggle()
		elif event.keycode==KEY_C:
			clear_ink()
	if event is InputEventMouseButton and event.button_index==MOUSE_BUTTON_LEFT:
		desktop_draw = event.pressed
