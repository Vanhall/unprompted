# Unprompted by Therefore Games. All Rights Reserved.
# https://patreon.com/thereforegames
# https://github.com/ThereforeGames/unprompted

# This script is intended to be used as an extension for Automatic1111's Stable Diffusion WebUI.

import gradio as gr

import modules.scripts as scripts
from modules.processing import process_images,fix_seed,Processed
from modules.shared import opts, cmd_opts, state, Options
from modules import sd_models
import lib_unprompted.shortcodes as shortcodes
from pathlib import Path
from enum import IntEnum,auto

from ui import settings

import sys
import os

settings.initialize()

base_dir = scripts.basedir()
sys.path.append(base_dir)
# Main object
from lib_unprompted.shared import Unprompted

Unprompted = Unprompted(base_dir)

WizardModes = IntEnum("WizardModes", ["FUNCTIONS","SHORTCODES"], start=0)

Unprompted.wizard_groups = [[{},{}] for _ in range(len(WizardModes))] # Two subdictionaries for txt2img and img2img
Unprompted.wizard_dropdown = None

Unprompted.wizard_function_files = []
Unprompted.wizard_function_names = []

def do_dry_run(string):
	Unprompted.log(string)
	# Reset vars
	Unprompted.shortcode_user_vars = {}
	unp_result = Unprompted.process_string(string)
	# Cleanup routines
	Unprompted.log("Entering cleanup routine...",False)
	for i in Unprompted.cleanup_routines:
		Unprompted.shortcode_objects[i].cleanup()
	return f"<strong>RESULT:</strong> {unp_result}"

def wizard_select_item(option,is_img2img,mode=WizardModes.SHORTCODES):
	Unprompted.wizard_dropdown.value = option

	this_list = Unprompted.wizard_groups[mode][int(is_img2img)]

	# Retrieve corresponding function filepath
	if (mode == WizardModes.FUNCTIONS): option = Unprompted.wizard_function_files[option]

	results = [gr.update(visible=(option == key)) for key in this_list.keys()]
	return results

def wizard_set_event_listener(obj):
	obj.change(fn=lambda val: wizard_update_value(obj,val),inputs=obj)

def wizard_update_value(obj,val):
	obj.value = val # TODO: Rewrite this with Gradio update function if possible

def wizard_generate_function(option,is_img2img,prepend="",append=""):
	filepath = os.path.relpath(Unprompted.wizard_function_files[option],f"{base_dir}/{Unprompted.Config.template_directory}")
	# Remove file extension
	filepath = os.path.splitext(filepath)[0]
	result = f"{Unprompted.Config.syntax.tag_start}file \"{filepath}\""
	filtered_functions = Unprompted.wizard_groups[WizardModes.FUNCTIONS][int(is_img2img)]
	group = filtered_functions[Unprompted.wizard_function_files[option]]

	def parse_children(obj,result):
		try:
			for gr_obj in obj.children:
				block_name = gr_obj.get_block_name()

				if block_name == "form" or block_name == "accordion":
					result = parse_children(gr_obj,result)
				else:
					if block_name == "label" or block_name == "markdown" or gr_obj.value is None or gr_obj.value == "": continue # Skip empty fields
					arg_name = gr_obj.label.split(" ")[-1] # Get everything after the last space
					# Skip special fields
					if (arg_name == "prompt"): continue

					this_val = str(Unprompted.autocast(gr_obj.value))

					if " " in this_val: this_val = f"\"{this_val}\"" # Enclose in quotes if necessary
					result += f" {arg_name}={this_val}"
		except: pass
		return(result)
	
	result = parse_children(group,result)

	# Closing bracket
	result += Unprompted.Config.syntax.tag_end

	return(prepend+result+append)

def wizard_generate_shortcode(option,is_img2img,prepend="",append=""):
	if hasattr(Unprompted.shortcode_objects[option],"wizard_prepend"): result = Unprompted.shortcode_objects[option].wizard_prepend
	else: result = Unprompted.Config.syntax.tag_start + option
	filtered_shortcodes = Unprompted.wizard_groups[WizardModes.SHORTCODES][int(is_img2img)]
	group = filtered_shortcodes[option]
	block_content=""

	def parse_children(obj,result):
		block_content = ""
		try:
			for gr_obj in obj.children:
				block_name = gr_obj.get_block_name()

				if block_name == "form" or block_name == "accordion":
					results = parse_children(gr_obj,result)
					block_content = results[0]
					result = results[1]
				elif gr_obj.label=="Content":
					block_content = gr_obj.value
				else:
					if block_name == "label" or block_name == "markdown" or gr_obj.value is None or gr_obj.value == "": continue # Skip empty fields		

					arg_name = gr_obj.label.split(" ")[-1] # Get everything after the last space

					# Rules
					if (arg_name == "prompt"): continue
					elif (arg_name == "str"):
						result += " \"" + str(gr_obj.value) + "\""
					elif (arg_name == "int"):
						result += " " + str(int(gr_obj.value))
					elif (arg_name == "verbatim"):
						result += " " + str(gr_obj.value)
					elif (block_name=="checkbox"):
						if gr_obj.value: result += " " + arg_name
					elif (block_name=="number" or block_name=="slider"): result += f" {arg_name}={Unprompted.autocast(gr_obj.value)}"
					elif (block_name=="textbox"):
						if len(gr_obj.value) > 0: result += f" {arg_name}=\"{gr_obj.value}\""
					else: result += f" {arg_name}=\"{gr_obj.value}\""		

		except: pass
		return([block_content,result])

	results = parse_children(group,result)
	block_content = results[0]
	result = results[1]

	# Closing bracket
	if hasattr(Unprompted.shortcode_objects[option],"wizard_append"): result += Unprompted.shortcode_objects[option].wizard_append
	else: result += Unprompted.Config.syntax.tag_end

	if hasattr(Unprompted.shortcode_objects[option],"run_block"):
		if (append and not block_content):
			block_content = append
			append = ""
			prepend = ""
		result += block_content + Unprompted.Config.syntax.tag_start + Unprompted.Config.syntax.tag_close + option + Unprompted.Config.syntax.tag_end

	return (prepend+result+append)

def get_markdown(file):
	file = Path(base_dir) / file
	lines = file.open(mode='r', encoding='utf-8').readlines()
	final_string = ""
	for line in lines:
		# Skip h1 elements
		if not line.startswith("# "): final_string += line
	return final_string

# Workaround for Gradio checkbox label+value bug https://github.com/AUTOMATIC1111/stable-diffusion-webui/issues/6109
def gradio_enabled_checkbox_workaround():
	return(Unprompted.Config.ui.enabled)

class Scripts(scripts.Script):
	allow_postprocess = True

	def title(self):
		return "Unprompted"

	def show(self, is_img2img):
		return scripts.AlwaysVisible

	def ui(self, is_img2img):
		with gr.Group():
			with gr.Accordion("Unprompted", open=Unprompted.Config.ui.open):
				is_enabled = gr.Checkbox(label="Enabled",value=gradio_enabled_checkbox_workaround)

				match_main_seed = gr.Checkbox(label="Synchronize Unprompted seed to main seed",value=True)
				setattr(match_main_seed,"do_not_save_to_config",True)

				unprompted_seed = gr.Number(label="Unprompted Seed",value=-1)
				setattr(unprompted_seed,"do_not_save_to_config",True)


				if (os.path.exists(f"{base_dir}/{Unprompted.Config.template_directory}/pro/demoncrawl_avatar_generator_v0.0.1/main{Unprompted.Config.txt_format}")): is_open = False
				else: is_open = True
				
				with gr.Accordion("🎉 Promo", open=is_open):
					plug = gr.HTML(label="plug",elem_id="promo",value=f'<a href="https://payhip.com/b/qLUX9" target="_blank"><img src="https://i.postimg.cc/nhchddM9/Demon-Crawl-Avatar-Generator-Box.png" style="float: left;width: 150px;margin-bottom:10px;"></a><h1 style="font-size: 20px;letter-spacing:0.015em;margin-top:10px;">NEW! The <strong>DemonCrawl Avatar Generator</strong> is out now.</h1><p style="margin:1em 0;">Create pixel art portraits in the style of the popular roguelite, DemonCrawl. Includes a custom Stable Diffusion model trained by the game\'s developer, as well as a custom GUI and the ability to randomize your prompts.</p><a href="https://payhip.com/b/qLUX9" target=_blank><button class="gr-button gr-button-lg gr-button-secondary" title="View premium assets for Unprompted">Learn More ➜</button></a>')

				with gr.Accordion("🧙 Wizard", open=Unprompted.Config.ui.wizard_open):
					if Unprompted.Config.ui.wizard_enabled:
						
						self.wizard_function_template = ""
						self.wizard_function_elements = []

						# Wizard UI shortcode parser for functions
						wizard_shortcode_parser = shortcodes.Parser(start=Unprompted.Config.syntax.tag_start, end=Unprompted.Config.syntax.tag_end, esc=Unprompted.Config.syntax.tag_escape, ignore_unknown=True, inherit_globals=False)

						def handler(keyword, pargs, kwargs, context, content):
							if "_new" in pargs:
								friendly_name = kwargs["_label"] if "_label" in kwargs else "Setting"
								block_name = kwargs["_ui"] if "_ui" in kwargs else "textbox"

								this_label = f"{friendly_name} {Unprompted.Config.syntax.wizard_delimiter} {pargs[0]}"

								# Produce UI based on type
								if (block_name == "textbox"):
									if "_placeholder" in kwargs: this_placeholder = kwargs["_placeholder"]
									else: this_placeholder = str(content)
									obj = gr.Textbox(label=this_label,max_lines=1,placeholder=this_placeholder)
								elif (block_name == "checkbox"):
									obj = gr.Checkbox(label=this_label,value=bool(int(content)))
								elif (block_name == "number"): obj = gr.Number(label=this_label,value=int(content),interactive=True)
								elif (block_name == "dropdown"): obj = gr.Dropdown(label=this_label,value=content,choices=kwargs["_choices"].split(Unprompted.Config.syntax.delimiter))
								elif (block_name == "radio"): obj = gr.Radio(label=this_label,choices=kwargs["_choices"].split(Unprompted.Config.syntax_delimiter),interactive=True)
								elif (block_name == "slider"):
									obj = gr.Slider(label=this_label,value=int(content),minimum=kwargs["_minimum"],maximum=kwargs["_maximum"],step=kwargs["_step"])
							
								setattr(obj,"do_not_save_to_config",True)
							return("")
						wizard_shortcode_parser.register(handler,"set",f"{Unprompted.Config.syntax.tag_close}set")

						def handler(keyword, pargs, kwargs, context, content):
							if "name" in kwargs: self.dropdown_item_name = kwargs["name"]
							# Fix content formatting for markdown
							content = content.replace("\\r\\n", "<br>") + "<br><br>"
							gr.Label(label="Options",value=f"{self.dropdown_item_name}")
							gr.Markdown(value=content)
							return("")
						wizard_shortcode_parser.register(handler,"template",f"{Unprompted.Config.syntax.tag_close}template")	

						def handler(keyword,pargs,kwargs,context):
							filepath = Path(os.path.relpath(filename,f"{base_dir}")).parent
							return(f"file/extensions/unprompted/{filepath}")
							
						wizard_shortcode_parser.register(handler,"base_dir")

						with gr.Tabs():
							filtered_functions = Unprompted.wizard_groups[WizardModes.FUNCTIONS][int(is_img2img)]
							filtered_shortcodes = Unprompted.wizard_groups[WizardModes.SHORTCODES][int(is_img2img)]

							def wizard_add_function(show_me=False):
								self.dropdown_item_name = filename
								with gr.Group(visible = show_me) as filtered_functions[filename]:
									# Render the text file's UI with special parser object
									wizard_shortcode_parser.parse(file.read())
									# Auto-include is always the last element
									gr.Checkbox(label="Auto-include this in prompt",value=False)
									# Add event listeners
									for child in filtered_functions[filename].children:
										if ("change" in dir(child) and child.get_block_name() != "label"):
											# use function to pass obj by reference
											wizard_set_event_listener(child)

							with gr.Tab("Functions"):
								import glob
								txt_files = glob.glob(f"{base_dir}/{Unprompted.Config.template_directory}/**/*.txt",recursive=True) if not is_img2img else Unprompted.wizard_function_files
								is_first = True
								
								functions_dropdown = gr.Dropdown(choices=[],label="Select function:",type="index")

								for filename in txt_files:
									with open(filename) as file:
										if is_img2img: wizard_add_function()
										else:
											first_line = file.readline()
											# Make sure this text file starts with the [template] tag - this identifies it as a valid function
											if first_line.startswith(f"{Unprompted.Config.syntax.tag_start}template"):
												file.seek(0) # Go back to start of file
												wizard_add_function(is_first)
												Unprompted.wizard_function_names.append(self.dropdown_item_name)
												Unprompted.wizard_function_files.append(filename)
												if (is_first):
													functions_dropdown.value = self.dropdown_item_name
													is_first = False
								
								# Refresh dropdown list
								functions_dropdown.choices = Unprompted.wizard_function_names

								if (len(filtered_functions) > 1):
									functions_dropdown.change(fn=wizard_select_item,inputs=[functions_dropdown,gr.Variable(value=is_img2img),gr.Variable(value=WizardModes.FUNCTIONS)],outputs=list(filtered_functions.values()))
								
								wizard_function_btn = gr.Button(value="Generate Shortcode")

							with gr.Tab("Shortcodes"):
								shortcode_list = list(Unprompted.shortcode_objects.keys())
								Unprompted.wizard_dropdown = gr.Dropdown(choices=shortcode_list,label="Select shortcode:",value=Unprompted.Config.ui.wizard_default_shortcode)
								
								for key in shortcode_list:
									if (hasattr(Unprompted.shortcode_objects[key],"ui")):
										with gr.Group(visible = (key == Unprompted.wizard_dropdown.value)) as filtered_shortcodes[key]:
											gr.Label(label="Options",value=f"{key}: {Unprompted.shortcode_objects[key].description}")
											if hasattr(Unprompted.shortcode_objects[key],"run_block"): gr.Textbox(label="Content",max_lines=2,min_lines=2)
											# Run the shortcode's UI function to populate
											Unprompted.shortcode_objects[key].ui(gr)
											# Auto-include is always the last element
											gr.Checkbox(label="Auto-include this in prompt",value=False)											
											# Add event listeners
											for child in filtered_shortcodes[key].children:
												if ("change" in dir(child) and child.get_block_name() != "label"):
													# use function to pass obj by reference
													wizard_set_event_listener(child)

								Unprompted.wizard_dropdown.change(fn=wizard_select_item,inputs=[Unprompted.wizard_dropdown,gr.Variable(value=is_img2img)],outputs=list(filtered_shortcodes.values()))
								
								wizard_shortcode_btn = gr.Button(value="Generate Shortcode")
							
							wizard_result = gr.HTML(label="wizard_result",value="",elem_id="unprompted_result")
							wizard_shortcode_btn.click(fn=wizard_generate_shortcode,inputs=[Unprompted.wizard_dropdown,gr.Variable(value=is_img2img),gr.Variable(value="<strong>RESULT:</strong> ")],outputs=wizard_result)
							wizard_function_btn.click(fn=wizard_generate_function,inputs=[functions_dropdown,gr.Variable(value=is_img2img),gr.Variable(value="<strong>RESULT:</strong> ")],outputs=wizard_result)

					else: gr.HTML(label="wizard_debug",value="You have disabled the Wizard in your config.")

					# wizard_autoinclude = gr.Checkbox(label="Auto-include in prompt",value=Unprompted.Config.ui.wizard_autoinclude)
					
				with gr.Accordion("📝 Dry Run", open=Unprompted.Config.ui.dry_run_open):
					dry_run_prompt = gr.Textbox(lines=2,placeholder="Test prompt",show_label=False)
					dry_run = gr.Button(value="Process Text")
					dry_run_result = gr.HTML(label="dry_run_result",value="",elem_id="unprompted_result")
					dry_run.click(fn=do_dry_run,inputs=dry_run_prompt,outputs=dry_run_result)
				
				with gr.Tab("💡 About"):
					about = gr.Markdown(value=get_markdown("docs/ABOUT.md").replace("$VERSION",Unprompted.VERSION))
					def open_folder(path):
						import platform
						import subprocess as sp
						path = os.path.normpath(path)
						if platform.system() == "Windows":
							os.startfile(path)
						elif platform.system() == "Darwin":
							sp.Popen(["open", path])
						else:
							sp.Popen(["xdg-open", path])						
					open_templates = gr.Button(value="📂 Open templates folder")
					open_templates.click(fn=lambda: open_folder(f"{base_dir}/{Unprompted.Config.template_directory}"),inputs=[],outputs=[])

				with gr.Tab("📣 Announcements"):
					announcements = gr.Markdown(value=get_markdown("docs/ANNOUNCEMENTS.md"))

				with gr.Tab("⏱ Changelog"):
					changelog = gr.Markdown(value=get_markdown("docs/CHANGELOG.md"))
				
				with gr.Tab("📘 Manual"):
					manual = gr.Markdown(value=get_markdown("docs/MANUAL.md"))

				with gr.Tab("🎓 Starter Guide"):
					guide = gr.Markdown(value=get_markdown("docs/GUIDE.md"))

				
		return [is_enabled,unprompted_seed,match_main_seed]
	
	def process(self, p, is_enabled=True, unprompted_seed=-1, match_main_seed=True, *args):
		if not is_enabled:
			return p

		if match_main_seed: 
			if p.seed == -1:
				from modules.processing import get_fixed_seed
				p.seed = get_fixed_seed(-1)
			Unprompted.log(f"Synchronizing seed with WebUI: {p.seed}")
			unprompted_seed = p.seed

		if unprompted_seed != -1:
			import random
			random.seed(unprompted_seed)
		
		def apply_prompt_template(string,template):
			return template.replace("*",string)

		# Reset vars
		original_prompt = p.all_prompts[0]

		# Process Wizard auto-includes
		if Unprompted.Config.ui.wizard_enabled:
			is_img2img = hasattr(p,"init_images")

			for mode in range(len(WizardModes)):
				groups = Unprompted.wizard_groups[mode][int(is_img2img)]
				for idx,key in enumerate(groups):
					group = groups[key]
					autoinclude_obj = group

					# In theory, this should always select the "autoinclude" checkbox at the bottom of the UI
					while hasattr(autoinclude_obj,"children"): autoinclude_obj = autoinclude_obj.children[-1]

					if (autoinclude_obj.value):
						if mode == WizardModes.SHORTCODES: original_prompt = wizard_generate_shortcode(key,is_img2img,"",original_prompt)
						elif mode == WizardModes.FUNCTIONS: original_prompt = wizard_generate_function(idx,is_img2img,"",original_prompt)

		original_negative_prompt = p.all_negative_prompts[0]
		Unprompted.shortcode_user_vars = {}

		# Extra vars
		Unprompted.shortcode_user_vars["batch_index"] = 0

		# Set up system var support - copy relevant p attributes into shortcode var object
		for att in dir(p):
			if not att.startswith("__") and att != "sd_model":
				Unprompted.shortcode_user_vars[att] = getattr(p,att)

		Unprompted.shortcode_user_vars["prompt"] = Unprompted.process_string(apply_prompt_template(original_prompt,Unprompted.Config.templates.default))
		Unprompted.shortcode_user_vars["negative_prompt"] = Unprompted.process_string(apply_prompt_template(Unprompted.shortcode_user_vars["negative_prompt"] if "negative_prompt" in Unprompted.shortcode_user_vars else original_negative_prompt,Unprompted.Config.templates.default_negative))

		# Apply any updates to system vars
		for att in dir(p):
			if not att.startswith("__") and att != "sd_model":
				setattr(p,att,Unprompted.shortcode_user_vars[att])	

		# Support loading a new checkpoint by name
		if "sd_model" in Unprompted.shortcode_user_vars:
			info = sd_models.get_closet_checkpoint_match(Unprompted.shortcode_user_vars["sd_model"])
			if (info): sd_models.load_model(info,None,None) # reload_model_weights(None,info)


		if p.seed is not None and p.seed != -1.0:
			if (Unprompted.is_int(p.seed)): p.seed = int(p.seed)
			p.all_seeds[0] = p.seed
		else:
			p.seed = -1
			p.seed = fix_seed(p)

		# Batch support
		if (Unprompted.Config.stable_diffusion.batch_support):
			for i, val in enumerate(p.all_prompts):
				if (i == 0):
					Unprompted.shortcode_user_vars["batch_index"] = i
					p.all_prompts[0] = Unprompted.shortcode_user_vars["prompt"]
					p.all_negative_prompts[0] = Unprompted.shortcode_user_vars["negative_prompt"]
				else:
					Unprompted.shortcode_user_vars = {}
					Unprompted.shortcode_user_vars["batch_index"] = i
					p.all_prompts[i] = Unprompted.process_string(apply_prompt_template(original_prompt,Unprompted.Config.templates.default))
					p.all_negative_prompts[i] = Unprompted.process_string(apply_prompt_template(Unprompted.shortcode_user_vars["negative_prompt"] if "negative_prompt" in Unprompted.shortcode_user_vars else original_negative_prompt,Unprompted.Config.templates.default_negative))

				Unprompted.log(f"Result {i}: {p.all_prompts[i]}",False)
		# Keep the same prompt between runs
		else:
			for i, val in enumerate(p.all_prompts):
				p.all_prompts[i] = Unprompted.shortcode_user_vars["prompt"]
				p.all_negative_prompts[i] = Unprompted.shortcode_user_vars["negative_prompt"]

		# Cleanup routines
		Unprompted.log("Entering Cleanup routine...",False)
		for i in Unprompted.cleanup_routines:
			Unprompted.shortcode_objects[i].cleanup()
		
		if unprompted_seed != -1: random.seed()

	# After routines
	def postprocess(self, p, processed, is_enabled=True, unprompted_seed=-1, match_main_seed=True):
		if not self.allow_postprocess or not is_enabled: return False # Prevents endless loop with some shortcodes
		self.allow_postprocess = False
		Unprompted.log("Entering After routine...")
		for i in Unprompted.after_routines:
			Unprompted.shortcode_objects[i].after(p,processed)
		self.allow_postprocess = True