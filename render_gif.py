bl_info = {
    "name": "GIF Exporter",
    "author": "Gemini",
    "version": (1, 0),
    "blender": (3, 0, 0),
    "location": "Properties > Render > GIF Export",
    "description": "Renders animation to PNG and converts to GIF using FFmpeg",
    "category": "Render",
}

import bpy
import os
import subprocess
from bpy.props import StringProperty, PointerProperty
from bpy.types import Operator, AddonPreferences, Panel

# ------------------------------------------------------------------------
#   Add-on Preferences (To set FFmpeg Path)
# ------------------------------------------------------------------------

class GIF_Preferences(AddonPreferences):
    bl_idname = __name__

    ffmpeg_path: StringProperty(
        name="FFmpeg Executable",
        subtype='FILE_PATH',
        description="Path to the ffmpeg.exe file",
        default=""
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "ffmpeg_path")
        layout.label(text="Please locate your ffmpeg executable.")

# ------------------------------------------------------------------------
#   Operator: Convert Existing PNGs to GIF
# ------------------------------------------------------------------------

class GIF_OT_convert(Operator):
    """Converts rendered PNG sequence to GIF using FFmpeg"""
    bl_idname = "gif.convert"
    bl_label = "Convert to GIF"

    def execute(self, context):
        scene = context.scene
        preferences = context.preferences.addons[__name__].preferences
        ffmpeg_exe = preferences.ffmpeg_path

        # Validate FFmpeg path
        if not os.path.exists(ffmpeg_exe) or not os.path.isfile(ffmpeg_exe):
            self.report({'ERROR'}, "FFmpeg path is invalid in Add-on Preferences")
            return {'CANCELLED'}

        # Get Render Path details
        filepath = scene.render.filepath
        abs_filepath = bpy.path.abspath(filepath)
        render_dir = os.path.dirname(abs_filepath)
        filename_prefix = os.path.basename(abs_filepath)
        
        # If output path is empty, default to tmp
        if not render_dir:
            render_dir = bpy.path.abspath("//")
            
        # Check for PNGs in the directory
        try:
            files = [f for f in os.listdir(render_dir) if f.endswith('.png')]
        except FileNotFoundError:
             self.report({'ERROR'}, "Render directory does not exist.")
             return {'CANCELLED'}

        if not files:
            self.report({'ERROR'}, "Error: No PNGs rendered at location")
            print("Error: No PNGs rendered at location")
            return {'CANCELLED'}

        # Setup GIF output directory
        gifs_dir = os.path.join(render_dir, "gifs")
        if not os.path.exists(gifs_dir):
            os.makedirs(gifs_dir)

        # Output GIF name
        # Normalize path (removes trailing slashes to ensure basename works)
        clean_path = os.path.normpath(render_dir)
        
        # Get Current Folder Name (e.g. "stand")
        current_folder_name = os.path.basename(clean_path)
        
        # Get Parent Folder Name (e.g. "character")
        parent_path = os.path.dirname(clean_path)
        parent_folder_name = os.path.basename(parent_path)
        
        # Construct Name: "character_stand.gif"
        if parent_folder_name and current_folder_name:
            gif_name = f"{parent_folder_name}_{current_folder_name}.gif"
        elif current_folder_name:
            # Fallback if at root of drive
            gif_name = f"{current_folder_name}.gif"
        else:
            # Total fallback
            gif_name = "animation.gif"
            
        output_file = os.path.join(gifs_dir, gif_name)

        # Construct FFmpeg Input pattern
        # Assumption: Blender Standard Naming (Name + Frame + .png)
        # We assume 4 digit padding by default in Blender
        input_pattern = os.path.join(render_dir, f"{filename_prefix}%04d.png")
        
        # Get Frame Rate
        fps = scene.render.fps

        # Construct Command
        # -y overwrites output without asking
        # -framerate sets input fps
        cmd = [
            ffmpeg_exe,
            '-y',
            '-framerate', str(fps),
            '-i', input_pattern,
            '-filter_complex', "[0:v]palettegen=stats_mode=diff[p];[0:v][p]paletteuse", 
            output_file
        ]
        
        # Basic command without palette generation (faster, lower quality):
        # cmd = [ffmpeg_exe, '-y', '-framerate', str(fps), '-i', input_pattern, output_file]

        try:
            self.report({'INFO'}, f"Converting to GIF at: {output_file}")
            # Run FFmpeg
            subprocess.run(cmd, check=True)
            self.report({'INFO'}, "GIF Conversion Finished!")
        except subprocess.CalledProcessError as e:
            self.report({'ERROR'}, f"FFmpeg Error: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

# ------------------------------------------------------------------------
#   Operator: Render (Modal) then Call Convert
# ------------------------------------------------------------------------

class GIF_OT_render_generate(Operator):
    """Renders animation then converts to GIF"""
    bl_idname = "gif.render_generate"
    bl_label = "Export as GIF"
    
    _timer = None

    def modal(self, context, event):
        if event.type == 'TIMER':
            # Check if the Render Job is still running
            if context.scene.gif_is_rendering:
                # Still rendering, do nothing
                pass
            else:
                # Render finished
                # Stop timer
                context.window_manager.event_timer_remove(self._timer)
                
                # Cleanup handler
                bpy.app.handlers.render_complete.remove(self.stop_render_flag)
                bpy.app.handlers.render_cancel.remove(self.stop_render_flag)
                
                # Trigger the conversion operator
                self.report({'INFO'}, "Render finished. Starting GIF conversion...")
                bpy.ops.gif.convert()
                
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        # Ensure format is PNG
        context.scene.render.image_settings.file_format = 'PNG'
        
        # Set a custom property to track render state
        context.scene.gif_is_rendering = True

        # Register handlers to toggle the flag when render ends
        bpy.app.handlers.render_complete.append(self.stop_render_flag)
        bpy.app.handlers.render_cancel.append(self.stop_render_flag)

        # Start Render (Invoke Default to allow UI updates)
        bpy.ops.render.render('INVOKE_DEFAULT', animation=True)

        # Start Modal Timer
        self._timer = context.window_manager.event_timer_add(1.0, window=context.window)
        context.window_manager.modal_handler_add(self)

        return {'RUNNING_MODAL'}
    
    # Callback to flip the flag
    def stop_render_flag(self, scene, context=None): # context is None in some Blender versions for handlers
        scene.gif_is_rendering = False

class GIF_OT_batch_process(Operator):
    """Recursively search for folders with PNGs and convert them"""
    bl_idname = "gif.batch_process"
    bl_label = "Batch Convert Folder"
    
    # Property to open the file browser
    directory: StringProperty(
        name="Root Folder",
        description="Select the root folder to search recursively",
        subtype='DIR_PATH'
    )

    def invoke(self, context, event):
        # Open the File Browser to select a folder
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root_folder = self.directory
        scene = context.scene
        
        # 1. Store the original render filepath to restore later
        original_filepath = scene.render.filepath
        
        folders_processed = 0

        self.report({'INFO'}, f"Starting recursive scan in: {root_folder}")

        # 2. Walk through directory tree
        for dirpath, dirnames, filenames in os.walk(root_folder):
            
            # Optimization: Don't process the 'gifs' output folders we create
            if "gifs" in dirnames:
                dirnames.remove("gifs")

            # Check for PNGs in current folder
            pngs = [f for f in filenames if f.lower().endswith('.png')]
            
            if pngs:
                # 3. Determine the Naming Prefix
                # The existing operator uses scene.render.filepath to determine the prefix.
                # We need to guess the prefix based on the actual files found.
                # e.g., if files are "MyAnim_0001.png", prefix is "MyAnim_"
                
                common_prefix = os.path.commonprefix(pngs)
                
                # If the common prefix includes digits (like "00"), strip them back 
                # so we get the base name (Blender usually pads with numbers at the end)
                base_prefix = common_prefix.rstrip('0123456789')
                
                # Construct a temporary filepath that simulates how Blender would output here
                # e.g., C:/FoundFolder/MyAnim_
                temp_filepath = os.path.join(dirpath, base_prefix)
                
                # Update scene variable so the existing operator knows what to do
                scene.render.filepath = temp_filepath
                
                print(f"Processing Folder: {dirpath} | Detected Prefix: '{base_prefix}'")

                try:
                    # 4. Call the EXISTING operator
                    # We pass 'EXEC_DEFAULT' so it runs immediately without UI invocation
                    res = bpy.ops.gif.convert('EXEC_DEFAULT')
                    
                    if 'FINISHED' in res:
                        folders_processed += 1
                        
                except Exception as e:
                    print(f"Skipping folder {dirpath} due to error: {e}")
                    continue

        # 5. Restore original filepath
        scene.render.filepath = original_filepath
        
        if folders_processed > 0:
            self.report({'INFO'}, f"Batch Complete. Processed {folders_processed} folders.")
        else:
            self.report({'WARNING'}, "Batch Complete. No PNG sequences found.")

        return {'FINISHED'}

# ------------------------------------------------------------------------
#   UI Panel
# ------------------------------------------------------------------------

class GIF_PT_panel(Panel):
    bl_label = "GIF Export"
    bl_idname = "RENDER_PT_gif_export"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.operator("gif.render_generate", text="Render & Convert to GIF", icon='RENDER_ANIMATION')
        layout.separator()
        layout.operator("gif.convert", text="Convert Existing to GIF", icon='FILE_MOVIE')
        # The new button
        layout.operator("gif.batch_process", text="Recursive Batch Convert", icon='FILE_FOLDER')

        if not context.preferences.addons[__name__].preferences.ffmpeg_path:
            layout.alert = True
            layout.label(text="FFmpeg path not set in Preferences!", icon='ERROR')

# ------------------------------------------------------------------------
#   Registration
# ------------------------------------------------------------------------

classes = (
    GIF_Preferences,
    GIF_OT_convert,
    GIF_OT_render_generate,
    GIF_PT_panel,
    GIF_OT_batch_process,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # Add a property to the scene to track render state for the modal operator
    bpy.types.Scene.gif_is_rendering = bpy.props.BoolProperty(default=False)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gif_is_rendering

if __name__ == "__main__":
    register()