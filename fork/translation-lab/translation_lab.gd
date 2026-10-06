# SPDX-License-Identifier: GPL-3.0-or-later
extends Node3D
const POSITIONS = [Vector3(-1,0,-1.2),Vector3(1,0,-1.2),Vector3(0,0,-2.5)]
const LANGUAGES = ["English","Japanese","Korean"]
const AUDIO = ["en_us","ja_jp","ko_kr"]
const PHRASES = ["窓を開けてください","Добро пожаловать","请在这里等候","좋은 하루 보내세요"]
const PANE_LANGUAGES = ["Japanese","Russian","Chinese","Korean"]
var rig
var pen
var speakers: Array = []
var panes: Array[Dictionary] = []
var was_palette = false
var rng = RandomNumberGenerator.new()
var ui_font: Font = preload("res://assets/fonts/Jupiteroid-Regular.ttf")
var pane_font: Font = preload("res://assets/fonts/Noto-Lab.ttf")
func _ready() -> void:
	rng.randomize()
	_make_room()
	rig = preload("res://player.gd").new()
	rig.name = "Player"
	add_child(rig)
	rig.position = Vector3(0,0,1.8)
	var colours = [Color("#45c9f0"),Color("#f2b456"),Color("#bb91ef")]
	for i in range(3):
		var speaker = preload("res://speaker.gd").new()
		speaker.name = LANGUAGES[i]+"Speaker"
		add_child(speaker)
		speaker.position = POSITIONS[i]
		speaker.setup(LANGUAGES[i],"res://assets/audio/"+AUDIO[i]+".wav",colours[i],ui_font)
		speakers.append(speaker)
	_make_panes()
	pen = preload("res://trail_pen.gd").new()
	pen.name = "TrailPen"
	pen.rig = rig
	add_child(pen)
	box(self,"PenStand",Vector3(0.38,0.95,0.38),Vector3(1.25,0.45,1),Color("#30465d"))
	label(self,"TRAIL PEN\nGrip to pick up",Vector3(1.25,1.23,1),40)
	_make_help()
	print("TRANSLATION_LAB_READY ",JSON.stringify(test_report()))
	await get_tree().create_timer(0.5).timeout
	rig.face_speakers()
func material(colour: Color) -> StandardMaterial3D:
	var m = StandardMaterial3D.new()
	m.albedo_color = colour
	m.roughness = 0.85
	return m
func box(parent: Node3D, node_name: String, size: Vector3, pos: Vector3, colour: Color) -> MeshInstance3D:
	var mesh = BoxMesh.new()
	mesh.size = size
	var node = MeshInstance3D.new()
	node.name = node_name
	node.mesh = mesh
	node.material_override = material(colour)
	parent.add_child(node)
	node.position = pos
	return node
func label(parent: Node3D, text: String, pos: Vector3, pixels: int = 64) -> Label3D:
	var node = Label3D.new()
	node.text = text
	node.font = ui_font
	node.font_size = pixels
	node.pixel_size = 0.0025
	node.outline_size = 0
	parent.add_child(node)
	node.position = pos
	return node
func _make_room() -> void:
	var world = WorldEnvironment.new()
	var env = Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color("#121c2b")
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color("#c9dcff")
	env.ambient_light_energy = 0.8
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	world.environment = env
	add_child(world)
	var sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-65,-25,0)
	add_child(sun)
	box(self,"Floor",Vector3(10,0.1,10),Vector3(0,-0.08,0),Color("#202f44"))
	for i in range(-5,6):
		box(self,"GridX"+str(i),Vector3(0.012,0.008,10),Vector3(i,-0.025,0),Color("#38506d"))
		box(self,"GridZ"+str(i),Vector3(10,0.008,0.012),Vector3(0,-0.025,i),Color("#38506d"))
	box(self,"BackWall",Vector3(10,3.6,0.1),Vector3(0,1.75,-4.9),Color("#182438"))
	label(self,"SPATIAL SUBTITLES / TRANSLATION LAB",Vector3(0,3,-4.82),100)
	label(self,"SPEECH  /  COLOURED TEXT  /  HANDWRITING",Vector3(0,2.65,-4.81),46)
func _make_panes() -> void:
	var positions = [Vector3(-2.7,1.8,-2.8),Vector3(2.7,1.8,-2.8),Vector3(-2.7,1.55,0.1),Vector3(2.7,1.55,0.1)]
	for i in range(4):
		var pane = Node3D.new()
		pane.name = PANE_LANGUAGES[i]+"Pane"
		add_child(pane)
		pane.position = positions[i]+Vector3(rng.randf_range(-0.15,0.15),rng.randf_range(-0.15,0.15),rng.randf_range(-0.15,0.15))
		pane.look_at(Vector3(0,pane.position.y,1),Vector3.UP,true)
		var board = box(pane,"ColourPanel",Vector3(2.1,0.55,0.04),Vector3.ZERO,Color.WHITE)
		board.material_override.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		var words = label(pane,PHRASES[i],Vector3(0,0,0.025),64)
		words.font = pane_font
		words.pixel_size = 0.002
		words.name = "SourceText"
		label(pane,PANE_LANGUAGES[i]+" / OCR",Vector3(0,-0.38,0.026),36)
		panes.append({"node":pane,"board":board,"words":words,"language":PANE_LANGUAGES[i]})
	randomize_panes()
func randomize_panes() -> void:
	for pane in panes:
		var hue = rng.randf()
		pane.board.material_override.albedo_color = Color.from_hsv(hue,rng.randf_range(0.45,0.8),rng.randf_range(0.12,0.25))
		pane.words.modulate = Color.from_hsv(fmod(hue+rng.randf_range(0.25,0.7),1),rng.randf_range(0.15,0.35),1)
func _make_help() -> void:
	var board = Node3D.new()
	add_child(board)
	board.position = Vector3(0,1.5,3.6)
	board.rotation.y = PI
	box(board,"InstructionsPanel",Vector3(3.6,1.45,0.04),Vector3.ZERO,Color("#142137"))
	label(board,"TRANSLATION TEST ROOM\nLeft stick: move / Right stick: snap turn\nApproach capsules to hear a full speech sample.\nRight grip near pen: pick up / put down\nRight trigger: draw / Left X: clear ink\nRight A: pane colours / Right B: return to start\nInk stays for 45 seconds.\nEnable local translation in Spatial Subtitles.",Vector3(0,0,0.03),38)
func _process(_delta: float) -> void:
	for speaker in speakers:
		speaker.proximity(rig.camera.global_position)
	var palette = rig.xr_active and rig.right.get_is_active() and rig.right.is_button_pressed("ax_button")
	if palette and not was_palette:
		randomize_panes()
	was_palette = palette
func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode==KEY_R:
			randomize_panes()
		elif event.keycode>=KEY_1 and event.keycode<=KEY_3:
			approach_speaker(event.keycode-KEY_1)
func approach_speaker(index: int) -> void:
	rig.move_head_to(POSITIONS[index]+Vector3(0,0,0.9))
func test_report() -> Dictionary:
	var report = {"xr_active":rig.xr_active,"speaker_count":speakers.size(),"pane_count":panes.size(),"ink_count":pen.points.size(),"pen_held":pen.held,"samples":[],"max_spacing":0.0}
	for i in range(speakers.size()):
		report.samples.append(speakers[i].report())
		for j in range(i):
			report.max_spacing = maxf(report.max_spacing,speakers[i].position.distance_to(speakers[j].position))
	return report
