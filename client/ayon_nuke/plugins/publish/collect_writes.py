from __future__ import annotations

import os
import nuke
import pyblish.api

from ayon_core.pipeline import publish

from ayon_nuke import api as napi


class CollectNukeWrites(
    pyblish.api.InstancePlugin, publish.ColormanagedPyblishPluginMixin
):
    """Collect all write nodes.

    This plugin collect the following data:

    - the "main" write node
       >>> instance.data["transientData"]["writeNode"] = nuke.Node
    - list of all write nodes for this instance (in case of multi-write nodes)
       >>> instance.data["transientData"]["writeNodes"] = list[nuke.Node]
    - expected files for the instance
       >>> instance.data["expectedFiles"] = list[str]

    """

    order = pyblish.api.CollectorOrder + 0.0021
    label = "Collect Writes"
    hosts = ["nuke", "nukeassist"]
    families = ["render", "prerender", "image"]

    settings_category = "nuke"

    # write node classes to collect
    write_node_classes = ["Write"]

    def process(self, instance) -> None:

        # compatibility. This is mainly focused on `renders`folders which
        # were previously not cleaned up (and could be used in read notes)
        # this logic should be removed and replaced with custom staging dir
        if instance.data.get("stagingDir_persistent") is None:
            instance.data["stagingDir_persistent"] = True

        group_node: nuke.Node = instance.data["transientData"]["node"]

        child_nodes = napi.get_instance_group_node_children(instance)
        write_nodes = [
            node
            for node in child_nodes
            if node.Class() in self.write_node_classes
        ]
        instance.data["transientData"]["writeNodes"] = write_nodes

        if not write_nodes:
            msg = f"Created node '{group_node.name()}' is missing write node!"
            self.log.warning(msg)
            return

        # todo: come up with a better way to determine the "main" write node
        write_node = write_nodes[0]
        instance.data["transientData"]["writeNode"] = write_node

        # get colorspace and add to version data
        colorspace = napi.get_colorspace_from_node(write_node)

        self._collect_frame_range_data(instance)
        self._collect_expected_files(instance)

        if instance.data["render_target"] in ["frames", "frames_farm"]:
            self._add_farm_instance_data(instance)

        # set additional instance data
        self._set_additional_instance_data(instance, render_target, colorspace)

    def _collect_frame_range_data(self, instance: pyblish.api.Instance) -> None:
        """Collect frame range data.

        Args:
            instance (pyblish.api.Instance): pyblish instance

        """
        write_node = instance.data["transientData"]["writeNode"]

        # Check in order of priority:
        # - override set in the publish settings (TODO)
        # - local override on the node
        # - previously collected frame range (TODO)
        # - frame range from the workfile
        if write_node["use_limit"].getValue():
            first_frame = int(write_node["first"].getValue())
            last_frame = int(write_node["last"].getValue())
        else:
            first_frame = int(nuke.root()["first_frame"].getValue())
            last_frame = int(nuke.root()["last_frame"].getValue())

        # only collect handle data for render instances
        if instance.data["productType"] == "render":
            handle_start = instance.context.data.get("handleStart", 0)
            handle_end = instance.context.data.get("handleEnd", 0)
        else:
            handle_start = 0
            handle_end = 0

        instance.data.update(
            {
                "handleStart": handle_start,
                "handleEnd": handle_end,
                "frameStart": first_frame + handle_start,
                "frameEnd": last_frame - handle_end,
                "frameStartHandle": first_frame,
                "frameEndHandle": last_frame,
            }
        )

    def _collect_expected_files(self, instance: pyblish.api.Instance) -> None:
        """Collect expected files.

        Args:
            instance (pyblish.api.Instance): pyblish instance

        """
        node = instance.data["transientData"]["writeNode"]
        start = instance.data["frameStart"]  # range includes handles
        end = instance.data["frameEnd"]

        # based on the docs  `nuke.filename` accepts a frame number as an argument
        # but this did not work for me (vincent-u, 24.02.2026, Nuke 15.2)
        filename = nuke.filename(node)
        if not filename:
            instance.data["expectedFiles"] = []
            # note: not sure if this is the best way to handle this
            # if introduces inconsistencies with the "path" and "outputDir" data
            return

        if "%" in filename:
            instance.data["expectedFiles"] = [filename % frame for frame in range(start, end + 1)]
        else: 
            # single file
            # TODO: if start != end we could raise an error here
            # this however is not the responsibility of this plugin
            instance.data["expectedFiles"] = [filename]

        instance.data["path"] = filename
        instance.data["outputDir"] = os.path.dirname(filename)
        instance.data["ext"] = os.path.splitext(filename)[1].lstrip(".")

    def _collect_colorspace_data(self, instance: pyblish.api.Instance) -> None:
        """Collect colorspace data.

        Args:
            instance (pyblish.api.Instance): pyblish instance

        """
        write_node = instance.data["transientData"]["writeNode"]
        colorspace = napi.get_colorspace_from_node(write_node)
        instance.data["colorspace"] = colorspace
        instance.data["color_channels"] = write_node["channels"].value()
        instance.data["versionData"] = {"colorspace": colorspace}

    def _set_additional_instance_data(
        self, instance, render_target, colorspace
    ):
        """Set additional instance data.

        Args:
            instance (pyblish.api.Instance): pyblish instance
            render_target (str): render target
            colorspace (str): colorspace
        """
        product_type = instance.data["productType"]

        # add targeted family to families
        targeted_family = f"{product_type}.{render_target}"
        instance.data["families"].append(targeted_family)
        self.log.debug(f"Appending render target to families: {targeted_family}")


        write_node = instance.data["transientData"]["writeNode"]
        # TODO: is this part of collection?
        if instance.data.get("stagingDir_is_custom", False):
            self.log.info(
                "Custom staging dir detected. Syncing write nodes output path."
            )
            napi.lib.writes_version_sync(write_node, self.log)

    def _add_farm_instance_data(self, instance):
        """Add farm publishing related instance data.

        Args:
            instance (pyblish.api.Instance): pyblish instance
        """

        # make sure rendered sequence on farm will
        # be used for extract review
        if not instance.data.get("review"):
            instance.data["useSequenceForReview"] = False

        # Farm rendering
        instance.data.update(
            {
                "transfer": False,
                "farm": True,  # to skip integrate
            }
        )
        self.log.info("Farm rendering ON ...")
