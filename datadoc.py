import util

import Priithon.Mrc

import numpy
import scipy.ndimage

## Maps dimensional axes to their labels.
DIMENSION_LABELS = ['Wavelength', 'Time', 'Z', 'Y', 'X']

## This class contains the data model that backs the rest of the program. 
# In other words, it's a wrapper around an MRC file (pixel data array) that
# provides functions for loading, saving, transforming, and slicing that
# array. 
class DataDoc:
    ## Instantiate the object.
    # \param image A loaded MRC image object.
    def __init__(self, image):
        ## Loaded MRC object. Note this is not just an array of pixels.
        self.image = image
        ## Header for the image data, which tells us e.g. what the ordering
        # of X/Y/Z/time/wavelength is in the MRC file.
        self.imageHeader = Priithon.Mrc.implement_hdr(image.Mrc.hdr._array.copy())
        ## Location the file is saved on disk.
        self.filePath = image.Mrc.path

        ## Number of wavelengths in the array.
        self.numWavelengths = self.imageHeader.NumWaves
        numTimepoints = self.imageHeader.NumTimes
        numX = self.imageHeader.Num[0]
        numY = self.imageHeader.Num[1]
        numZ = self.imageHeader.Num[2] // (self.numWavelengths * numTimepoints)
        ## Size in pixels of the data.
        self.size = numpy.array([self.numWavelengths, numTimepoints, numZ, numY, numX], dtype = numpy.int)
        ## 5D array of pixel data, indexed as 
        # self.imageArray[wavelength][time][z][y][x]
        # In other words, in WTZYX order. In general we try to treat 
        # Z and time as "just another axis", but wavelength is dealt with
        # specially.
        self.imageArray = self.getImageArray()
        ## Datatype of our array.
        self.dtype = self.imageArray.dtype.type

        ## Averages for each wavelength, used to provide fill values when
        # taking slices.
        self.averages = []
        for wavelength in xrange(self.numWavelengths):
            self.averages.append(self.imageArray[wavelength].mean())

        ## Lower boundary of the cropped data.
        self.cropMin = numpy.array([0, 0, 0, 0, 0], numpy.int32)
        ## Upper boundary of the cropped data.
        self.cropMax = numpy.array(self.size, numpy.int32)
        
        ## Index of the single pixel that is visible in all different data
        # views.
        self.curViewIndex = numpy.array(self.size / 2, numpy.int)
        # Initial time view is at 0
        self.curViewIndex[1] = 0

        ## Parameters for transforming different wavelengths so they align 
        # with each other. Order is dx, dy, dz, angle, zoom
        self.alignParams = numpy.zeros((self.size[0], 5), numpy.float32)
        # Default zoom to 1.0
        self.alignParams[:,4] = 1.0


    ## Convert the loaded MRC object into a 5D array of pixel data. How we
    # do this depends on the ordering of X/Y/Z/time/wavelength in the file --
    # the problem being that the shape of the array in the file is not padded
    # out with dimensions that are length 1 (e.g. a file with 1 wavelength).
    # So we pad out the default array until it is five-dimensional, and then
    # rearrange its axes until its ordering is WTZYX.
    def getImageArray(self):
        # This is a string describing the dimension ordering as stored in 
        # the file.
        sequence = self.image.Mrc.axisOrderStr()
        dimOrder = ['w', 't', 'z', 'y', 'x']
        vals = zip(self.size, dimOrder)
        dataCopy = numpy.array(self.image)
        # Find missing axes and pad the array until it has 5 axes.
        for val, key in vals[:2]:
            # The wavelength and time dimensions are left off if they have
            # length 1.
            if val == 1:
                # The array is missing a dimension, so pad it out.
                dataCopy = numpy.expand_dims(dataCopy, -1)
                sequence = sequence + key
        # Generate a list of how we need to reorder the axes.
        ordering = []
        for val, key in vals:
            ordering.append(sequence.index(key))

        return dataCopy.transpose(ordering)


    ## Generate a 2D slice of our data in each wavelength. Since our data is 5D
    # (wavelength/time/Z/Y/X), there are three axes to be perpendicular to,
    # one of which is always wavelength. The "axes" argument maps the other
    # two axis indices to the coordinate the slice should pass through.
    # E.g. passing in {1: 10, 2: 32} means to take a WXY slice at timepoint
    # 10 through Z index 32.
    # This was fairly complicated for me to figure out, since I'm not a 
    # scientific programmer, so I'm including my general process here:
    # - Figure out which axes the slice cuts across, and generate an array
    #   of the appropriate shape to hold the results.
    # - Create an array of similar size augmented with a length-4 dimension.
    #   This array holds XYZ coordinates for each pixel in the slice; the 4th
    #   index holds a 1 (so that we can use a 4x4 affine transformation matrix
    #   to do rotation and offsets in the same pass). For example, an XY slice
    #   at Z = 5 would look something like this:
    # [  [0, 0, 5]  [0, 1, 5]  [0, 2, 5] ...
    # [  [1, 0, 5]  ...
    # [  [2, 0, 5]
    # [  ...
    # [
    # - Subtract the XYZ center off of the coordinates so that when we apply
    #   the rotation transformation, it's done about the center of the dataset
    #   instead of the corner.
    # - Multiply the inverse transformation matrix by the coordinates.
    # - Add the center back on.
    # - Chop off the dummy 1 coordinate, reorder to ZYX, and prepend the time
    #   dimension.
    # - Pass the list of coordinates off to numpy.map_coordinates so it can
    #   look up actual pixel values.
    # - Reshape the resulting array to match the slice shape.
    def takeSlice(self, axes, shouldTransform = True, order = 1):
        if shouldTransform:
            targetShape = []
            targetAxes = []
            presets = [-1] * 5
            # Generate an array to hold the slice. Note this includes all
            # wavelengths.
            for i, size in enumerate(self.size):
                if i not in axes:
                    targetShape.append(size)
                    targetAxes.append(i)
                else:
                    presets[i] = axes[i]

            # Create a matrix of size (NxMx3) where N and M are the width
            # and height of the desired slice, and the remaining dimension 
            # holds the desired XYZ coordinates for each pixel in the slice,
            # pre-transform. Note this is wavelength-agnostic.
            targetCoords = numpy.empty(targetShape[1:] + [3])
            haveAlreadyResized = False
            # Axes here are in WTZYX order, so we need to reorder them to XYZ.
            for axis in [2, 3, 4]:
                if axis in targetAxes:
                    basis = numpy.arange(self.size[axis])
                    if (self.size[axis] == targetCoords.shape[0] and 
                            not haveAlreadyResized):
                        # Reshape into a column vector. We only want to do this
                        # once, but unfortunately can't tell solely with the 
                        # length of the array in the given axis since it's not
                        # uncommon for e.g. X and Y to have the same size.
                        basis.shape = self.size[axis], 1
                        haveAlreadyResized = True
                    targetCoords[:,:,4 - axis] = basis
                else:
                    targetCoords[:,:,4 - axis] = axes[axis]
            return self.mapCoords(targetCoords, targetShape, axes, order)
        else:
            # Simply take an ordinary slice.
            # Ellipsis is a builtin keyword for the full-array slice. Who knew?
            slices = [Ellipsis]
            for axis in xrange(1, 5):
                if axis in axes:
                    slices.append(axes[axis])
                else:
                    slices.append(Ellipsis)
            return self.imageArray[slices]


    ## Inverse-transform the provided coordinates and use them to look up into
    # our data, to generate a transformed slice of the specified shape along
    # the specified axes.
    # \param targetCoords 4D array of WXYZ coordinates.
    # \param targetShape Shape of the resulting slice.
    # \param axes Axes the slice cuts along.
    # \param order Spline order to use when mapping. Lower is faster but 
    #        less accurate
    def mapCoords(self, targetCoords, targetShape, axes, order):
        # Reshape into a 2D list of the desired coordinates
        targetCoords.shape = numpy.product(targetShape[1:]), 3
        # Insert a dummy 4th dimension so we can use translation in an 
        # affine transformation.
        tmp = numpy.empty((targetCoords.shape[0], 4))
        tmp[:,:3] = targetCoords
        tmp[:,3] = 1
        targetCoords = tmp

        transforms = self.getTransformationMatrices()
        inverseTransforms = [numpy.linalg.inv(matrix) for matrix in transforms]
        transposedCoords = targetCoords.T
        # XYZ center, which needs to be added and subtracted from the 
        # coordinates before/after transforming so that rotation is done
        # about the center of the image.
        center = self.size[2:][::-1].reshape(3, 1) / 2.0
        transposedCoords[:3,:] -= center
        result = numpy.zeros(targetShape, dtype = self.dtype)
        for wavelength in xrange(self.numWavelengths):
            # Transform the coordinates according to the alignment 
            # parameters for the specific wavelength.
            transformedCoords = numpy.dot(inverseTransforms[wavelength],
                    transposedCoords)
            transformedCoords[:3,:] += center

            # Chop off the trailing 1, reorder to ZYX, and insert the time
            # coordinate.
            tmp = numpy.zeros((4, transformedCoords.shape[1]), dtype = numpy.float)
            for i in xrange(3):
                tmp[i + 1] = transformedCoords[2 - i]

            transformedCoords = tmp
            if 1 not in axes:
                # User wants a cut across time.
                transformedCoords[0,:] = numpy.arange(self.size[1]).repeat(
                        transformedCoords.shape[1] / self.size[1])
            else:
                transformedCoords[0,:] = axes[1]

            resultVals = scipy.ndimage.map_coordinates(
                    self.imageArray[wavelength], transformedCoords, 
                    order = order, cval = self.averages[wavelength])
            resultVals.shape = targetShape[1:]
            result[wavelength] = resultVals
            
        return result        


    ## Return the value for each wavelength at the specified TZYX coordinate, 
    # taking transforms into account. Also return the transformed coordinates.
    # \todo This copies a fair amount of logic from self.mapCoords.
    def getValuesAt(self, coord):
        transforms = self.getTransformationMatrices()
        inverseTransforms = [numpy.linalg.inv(matrix) for matrix in transforms]
        # Reorder to XYZ and add a dummy 4th dimension.
        transposedCoord = numpy.array([[coord[3]], [coord[2]], 
            [coord[1]], [1]])
        # XYZ center, which needs to be added and subtracted from the 
        # coordinates before/after transforming so that rotation is done
        # about the center of the image.
        center = (self.size[2:][::-1] / 2.0).reshape(3, 1)
        transposedCoord[:3] -= center
        resultVals = numpy.zeros(self.numWavelengths, dtype = self.dtype)
        resultCoords = numpy.zeros((self.numWavelengths, 4))
        for wavelength in xrange(self.numWavelengths):
            # Transform the coordinates according to the alignment 
            # parameters for the specific wavelength.
            transformedCoord = numpy.dot(inverseTransforms[wavelength],
                    transposedCoord)
            transformedCoord[:3,:] += center
            # Reorder to ZYX and insert the time dimension.
            transformedCoord = numpy.array([coord[0], 
                    transformedCoord[2], transformedCoord[1], 
                    transformedCoord[0]],
                dtype = numpy.int
            )
            resultCoords[wavelength,:] = transformedCoord
            transformedCoord.shape = 4, 1

            resultVals[wavelength] = scipy.ndimage.map_coordinates(
                    self.imageArray[wavelength], transformedCoord, 
                    order = 1, cval = self.averages[wavelength])[0]
        return resultVals, resultCoords


    ## Take a default slice through our view indices perpendicular to the 
    # given axes.
    def takeDefaultSlice(self, perpendicularAxes, shouldTransform = True):
        targetCoords = self.getSliceCoords(perpendicularAxes)
        return self.takeSlice(targetCoords, shouldTransform)


    ## Generate a 4D transformation matrix based on self.alignParams for
    # each wavelength.
    def getTransformationMatrices(self):
        result = []
        for wavelength in xrange(self.numWavelengths):
            dx, dy, dz, angle, zoom = self.alignParams[wavelength]
            angle = angle * numpy.pi / 180.0
            cosTheta = numpy.cos(angle)
            sinTheta = numpy.sin(angle)
            transform = zoom * numpy.array(
                    [[cosTheta, sinTheta, 0, dx],
                     [-sinTheta, cosTheta, 0, dy],
                     [0, 0, 1, dz],
                     [0, 0, 0, 1]])
            result.append(transform)
        return result


    ## Return true if there is any Z motion in any wavelength's alignment
    # parameters.
    def hasZMotion(self):
        return numpy.any(self.alignParams[:,2] != 0)


    ## Return true if there is any non-default transformation.
    def hasTransformation(self):
        for i, nullValue in enumerate([0, 0, 0, 0, 1]):
            if not numpy.all(self.alignParams[:,i] == nullValue):
                # At least one wavelength has a transformation here.
                return True
        return False


    ## Apply our alignment parameters to the data, then crop them, and either
    # return the result for the specified wavelength(s), or save the result
    # to the specified file path. If no wavelengths are specified, use them all.
    # \todo All of the logic dealing with the MRC file writing is basically
    # copied from the old imdoc module, and I don't claim to understand why it
    # does what it does.
    # \todo The extended header is not preserved. On the flip side, according
    # to Eric we don't currently use the extended header anyway, so it was
    # just wasting space.
    def alignAndCrop(self, wavelengths = [], timepoints = [], 
            savePath = None):
        if not wavelengths:
            wavelengths = range(self.size[0])
        if not timepoints:
            timepoints = range(self.cropMin[1], self.cropMax[1])

        # Generate the cropped shape of the file.
        croppedShape = [len(wavelengths)]
        for min, max in zip(self.cropMin[1:], self.cropMax[1:]):
            croppedShape.append(max - min)
        # Reorder to time/wavelength/z/y/x for saving.
        croppedShape[0], croppedShape[1] = croppedShape[1], croppedShape[0]
        croppedShape = tuple(croppedShape)

        newHeader = Priithon.Mrc.makeHdrArray()
        Priithon.Mrc.initHdrArrayFrom(newHeader, self.imageHeader)
        newHeader.Num = (croppedShape[4], croppedShape[3], 
                croppedShape[2] * croppedShape[1] * croppedShape[0])
        newHeader.NumTimes = self.size[1]
        newHeader.NumWaves = len(wavelengths)
        # Size of the extended header -- forced to zero for now.
        newHeader.next = 0
        # Ordering of data in the file; 2 means z/w/t
        newHeader.ImgSequence = 2
        newHeader.PixelType = Priithon.Mrc.dtype2MrcMode(numpy.float32)

        if not savePath:
            outputArray = numpy.empty(croppedShape, numpy.float32)
        else:
            if self.filePath == savePath:
                # \todo Why do we do this?
                del self.image.Mrc

            # Write out the header.
            outputFile = file(savePath, 'wb')
            outputFile.write(newHeader._array.tostring())

        # Slices to use to crop out the 3D volume we want to use for each
        # wave-timepoint pair.
        volumeSlices = []
        for min, max in zip(self.cropMin[2:], self.cropMax[2:]):
            volumeSlices.append(slice(min, max))
        
        for timepoint in timepoints:
            #IMD 20111011 We need to interate over the wavelengths in
            #the alignment set rather than Chnaging these two lines
            #alows us to not overrun the outputArray when doing
            #alignment.
            for wavelength in range(len(wavelengths)):
                volume = self.imageArray[wavelengths[wavelength]][timepoint]
                
                dx, dy, dz, angle, zoom = self.alignParams[wavelength]
                if dz and self.size[2] == 1:
                    # HACK: no Z translate in 2D files. Even
                    # infinitesimal translates will zero out the entire slice,
                    # otherwise.
                    dz = 0
                if dx or dy or dz or angle or zoom != 1:
                    # Transform the volume.
                    angle = angle * numpy.pi / 180.0
                    volume = util.transformArray(
                            volume, dx, dy, dz, angle, zoom
                    )
                # Crop to the desired shape.
                volume = volume[volumeSlices].astype(numpy.float32)

                if not savePath:
                    # IMD 11/10/2011 If doing alignment of more than 2
                    # wavelengths then we need to make sure that the
                    # wavelength doesnt go off the end of the
                    # outputArray, solved by changing wavelength for
                    # loop above.
                    outputArray[timepoint, wavelength] = volume
                else:
                    # Write to the file.
                    for i, zSlice in enumerate(volume):
                        outputFile.write(zSlice)

        if not savePath:
            # Reorder to WTZYX since that's what the user expects.
            return outputArray.transpose([1, 0, 2, 3, 4])
        else:
            outputFile.close()


    ## Return the number of the section for the extended header, based on the 
    # provided indices and the order in which data is stored, as indicated
    # by the ImgSequence parameter:
    # 0: ztw
    # 1: wzt
    # 2: zwt
    def getExtendedHeaderIndex(self, timepoint, wavelength, zIndex):
        sequence = self.imageHeader.ImgSequence
        numTimepoints = self.size[1]
        numZ = self.size[2]
        if sequence == 0:
            return wavelength * numTimepoints * numZ + timepoint * numZ + zIndex
        if sequence == 1:
            return timepoint * numZ * self.numWavelengths + zIndex * self.numWavelengths + wavelength
        # Assume sequence is 2.
        return timepoint * self.numWavelengths * numZ + wavelength * numZ + zIndex


    ## Get the size of a slice in the specified dimensions. Dimensions are as
    # ordered in self.size
    def getSliceSize(self, axis1, axis2):
        return numpy.array([self.size[axis1], self.size[axis2]])


    ## Get the default set of target coords needed to call takeSlice, based on
    # self.curViewIndex. The input is a size-2 list of axes that the view is
    # normal to.
    def getSliceCoords(self, axes):
        return dict([(axis, self.curViewIndex[axis]) for axis in axes])


    ## Move self.curViewIndex by the specified amount, ensuring that we stay
    # in-bounds.
    def moveSliceLines(self, offset):
        for i, delta in enumerate(offset):
            targetVal = self.curViewIndex[i] + delta
            if targetVal >= 0 and targetVal < self.size[i]:
                self.curViewIndex[i] = targetVal


    ## Move the crop box by the specified amount, ensuring that we stay 
    # in-bounds.
    def moveCropbox(self, offset, isMin):
        if isMin:
            self.cropMin += offset
            for i, val in enumerate(self.cropMin):
                self.cropMin[i] = max(0, min(self.size[i], val))
        else:
            self.cropMax += offset
            for i, val in enumerate(self.cropMax):
                self.cropMax[i] = max(0, min(self.size[i], val))
            

    ## Multiply the given XYZ offsets by our pixel sizes to get offsets
    # in microns.
    def convertToMicrons(self, offsets):
        return numpy.multiply(offsets, self.imageHeader.d)


    ## As convertToMicrons, but in reverse.
    def convertFromMicrons(self, offsets):
        return numpy.divide(offsets, self.imageHeader.d)