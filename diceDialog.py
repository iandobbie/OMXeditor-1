# TODO - FIXME - DEPRECATED!!!
import numpy
import os
import wx

import datadoc
import util

## This dialog allows a user to cut up a file into multiple sub-files, each of
# which contain a subset of the original file's data.
class DiceDialog(wx.Dialog):
    ## Instantiate the dialog. In addition to the standard arguments we also
    # need the DataDoc that we'll be cutting up into bits.
    def __init__(
            self, parent, dataDoc, size = wx.DefaultSize, 
            pos = wx.DefaultPosition, 
            style = wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
            ):
        wx.Dialog.__init__(self, parent, -1, "Dice file", pos, size, style)

        self.dataDoc = dataDoc

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        explanationText = wx.StaticText(self, -1, 
                "This dialog allows to you split a file with multiple " + 
                "timepoints into multiple sub-files each with one timepoint, " +
                "while applying alignment and crop parameters.",
                size = (400, 40))
        mainSizer.Add(explanationText, 0, wx.ALIGN_CENTRE | wx.ALL, 10)

        pixelSizes = self.dataDoc.imageHeader.d

        # Allow the user to correct pixel sizes, for now.
        rowSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.XYPixelSize = util.addLabeledInput(self, rowSizer, 
                label = "XY pixel size:", defaultValue = str(pixelSizes[0]))
        self.ZPixelSize = util.addLabeledInput(self, rowSizer,
                label = "Z pixel size:", defaultValue = str(pixelSizes[2]))
        mainSizer.Add(rowSizer, 0, wx.ALL, 5)

        rowSizer = wx.BoxSizer(wx.HORIZONTAL)
        rowSizer.Add((1, 1), 1, wx.EXPAND)
        button = wx.Button(self, wx.ID_CANCEL, "Cancel")
        button.Bind(wx.EVT_BUTTON, self.OnCancel)
        rowSizer.Add(button)
        button = wx.Button(self, wx.ID_OK, "Start")
        button.Bind(wx.EVT_BUTTON, self.OnStart)
        rowSizer.Add(button)
        mainSizer.Add(rowSizer, 0, wx.ALL, 10)

        self.SetSizerAndFit(mainSizer)

        self.SetPosition((400, 300))
        self.Show()


    ## Cut the file up and save each resulting sub-file to a directory the user
    # chooses.
    def OnStart(self, event):
        alignParams = self.dataDoc.alignParams

        saveDialog = wx.DirDialog(self,
                "Please select where you wish to save the files")
        if saveDialog.ShowModal() != wx.ID_OK:
            return

        savePath = os.path.abspath(saveDialog.GetPath())

        progress = wx.ProgressDialog(parent = self,
                title = "Aligning and cropping files",
                message = " " * 100, # To make the dialog big enough.
                maximum = self.dataDoc.size[1],
                style = wx.PD_AUTO_HIDE | wx.PD_ESTIMATED_TIME |
                        wx.PD_REMAINING_TIME | wx.PD_SMOOTH)
        progress.Show()

        ## This is the MRC object we will use to instantiate new 
        # DataDocs with only one timepoint each.
        fullImage = self.dataDoc.image

        # Make a copy of the DataDoc so we can freely change values without
        # affecting the original. \todo This is wasteful of memory.
        doc = datadoc.DataDoc(fullImage)
        # Can't crop in time, obviously.
        cropMin = numpy.array(self.dataDoc.cropMin)
        cropMin[1] = 0
        cropMax = numpy.array(self.dataDoc.cropMax)
        cropMax[1] = 1
        doc.cropMin = cropMin
        doc.cropMax = cropMax

        XYSize = float(self.XYPixelSize.GetValue())
        ZSize = float(self.ZPixelSize.GetValue())
        doc.imageHeader.d = numpy.array([XYSize, XYSize, ZSize])

        for i in xrange(self.dataDoc.size[1]):
            progress.Update(i)

            targetFilename = os.path.join(savePath,
                    os.path.basename(self.dataDoc.filePath) + '-t%03d' % i)
            doc.alignAndCrop(savePath = targetFilename, timepoints = [i])
        progress.Update(self.dataDoc.size[1], "All done!")

        self.Hide()
        self.Destroy()


    def OnCancel(self, event):
        self.Hide()
        self.Destroy()

