# SPDX-License-Identifier: GPL-3.0-or-later
extends Node3D
var player: AudioStreamPlayer3D
var title: Label3D
var state: Label3D
var language = ""
var inside = false
var trigger_count = 0
func setup(lang: String, file: String, colour: Color, font: Font) -> void:
	language = lang
	var mesh = CapsuleMesh.new()
	mesh.radius = 0.23
	mesh.height = 1.35
	var capsule = MeshInstance3D.new()
	capsule.mesh = mesh
	var mat = StandardMaterial3D.new()
	mat.albedo_color = colour
	capsule.material_override = mat
	add_child(capsule)
	capsule.position.y = 0.8
	title = Label3D.new()
	title.text = lang
	title.font = font
	title.font_size = 64
	title.pixel_size = 0.0028
	title.outline_size = 0
	title.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	add_child(title)
	title.position.y = 1.75
	state = Label3D.new()
	state.font = font
	state.font_size = 40
	state.pixel_size = 0.002
	state.outline_size = 0
	state.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	add_child(state)
	state.position.y = 1.5
	player = AudioStreamPlayer3D.new()
	player.name = "Speech"
	player.stream = load(file)
	player.unit_size = 1.3
	player.max_distance = 8
	player.volume_db = -6
	player.attenuation_model = AudioStreamPlayer3D.ATTENUATION_INVERSE_DISTANCE
	add_child(player)
	player.position.y = 1.35
func proximity(head: Vector3) -> void:
	var distance = Vector2(head.x, head.z).distance_to(Vector2(global_position.x, global_position.z))
	if distance <= 1.1 and not inside:
		inside = true
		if not player.playing:
			player.play()
			trigger_count += 1
			print("PROXIMITY_PLAY ", language, " count=", trigger_count)
	elif distance >= 1.45:
		inside = false
	state.text = ("Speaking · %.1f s" % player.get_playback_position()) if player.playing else "Approach within 1.1 m"
func report() -> Dictionary:
	return {"language":language, "duration":player.stream.get_length(), "playing":player.playing, "trigger_count":trigger_count}
