#/usr/bin/env python
# -*- coding: ascii -*-

# Copyright (C) 2014 Graeme Ball <graemeball@googlemail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
cmos_stripe_filter
------------------

Simple python2 CLI script to Fourier Filter CMOS camera Stripes.

Requires DeltaVision data as input, writes filename_FFS.dv output.
"""

__author__ = "Graeme Ball (graemeball@googlemail.com)"
__copyright__ = "Copyright (c) 2014 Graeme Ball"
__license__ = "GPL v3"  # http://www.gnu.org/licenses/gpl.txt

import sys
import os
import shutil
import numpy as np
from Priithon import Mrc


def main():
    """Collect input filename, create output file, and filter each slice"""

    input_path = sys.argv[1]
    if not os.path.exists(input_path):
        print "Cannot find file: " + input_path
        sys.exit()
    else:
        # Fourier Filter Stripes: copy to new file (data will be overwritten)
        output_path = addTag(input_path, "FFS")
        shutil.copy2(input_path, output_path)
        # NB. Mrc is a special numpy ndarray with extra metadata attached
        fMrc = Mrc.bindFile(output_path, writable=1)
        # make a view of the data ndarray that is a flat list of XY slices
        nplanes = reduce(lambda x, y: x * y, fMrc.shape[:-2])
        ny, nx = fMrc.shape[-2:]
        xy_slices = fMrc.reshape((nplanes, ny, nx))
        # filter out stripes from each slice of the whole stack (in-place)
        for p in range(nplanes):
            xy_slices[p,:,:] = filter_stripes(xy_slices[p,:,:])


def addTag(file_path, tag):
    """Create new file path including tag before .extension"""
    path, ext = os.path.splitext(file_path)
    return path + "_" + tag + ext


def filter_stripes(yx_slice, horizontal=True):
    """Filter out (remove) horizontal or vertical stripes in 2D image data.

    Parameters
    ----------
    yx_slice : numpy.ndarray
        An 2D image data slice, dimension order Y then X
        
    horizontal : boolean
        Stripes are horizontal? (else vertical)

    Returns
    ------
    numpy.ndarray
        The filtered 2D image data slice

    """
    img_f = np.fft.fftshift(np.fft.fft2(yx_slice.copy()))
    if horizontal:
        xc = img_f.shape[1] / 2  # x center in freq space (zero x freq / offset)
        img_f[:, xc:xc+1] = 1.0  # suppress vertical stripe in *freq* space
    else:
        yc = img_f.shape[0] / 2  # y center in freq space (zero y freq / offset)
        img_f[yc:yc+1, :] = 1.0  # suppress horizontal stripe in *freq* space
    img_filtered = np.fft.ifft2(np.fft.ifftshift(img_f))
    img_filtered = img_filtered.real
    residual = yx_slice - img_filtered
    # add offset back to filtered image to prevent negative intensities
    return img_filtered + residual.mean()


if __name__ == '__main__':
    main()
