import imp
import logging
from math import ceil, floor
import numpy as np

### Optional imports ###
try:
    import cv2
    use_cv2 = True
except ImportError:
    use_cv2 = False

try:
    from numba import (jit, guvectorize, vectorize, float64, float32,
                       int16, uint16, double)
    use_numba = True
except ImportError:
    use_numba = False

logger = logging.getLogger(__name__)

def downsample(arr,
               shape = None,
               factor = None,
               extent = None,
               method = 'aggregate',
               no_data_value = None,
               source = None):
    """
    Downsampling an array with either a custiom routine from below or
    cv2 based on which options is available for a given downsample.

    Parameters
    ----------
    arr : array_like
        Image data in a two or three dimension array in band-first order.
    shape : list
        Shape of the desired output array.
    factor : integer, float, or length two iterable
        Factor by which to scale the image (must be less than one).
    extent : length four array-like
        List of upper-left and lower-right corner coordinates for the edges
        of the resample.  Coordinates should be expressed in pixel space.
        i.e. [-1,-2,502,501]
    method : strings
        Method to use for the downsample - 'aggregate', 'nearest', 'max', or
        'min.
    no_data_value : int
        Data value to treat as no_data
    source : strings
        Package to use for algorithm - opencv ('cv2') or custom 'numba' below.

    Returns
    -------
    ndarray
        Three dimensional numpy array of downsampled data
    """

    # If arr comes in as a 2D array, assume this is a single band from
    # a 3D image and reshape accordingly
    if len(arr.shape) == 2:
        arr = arr[np.newaxis, :, :]

    # Check input parameters
    if shape and factor:
        raise ValueError('Either shape or factor can be specificed, not both.')

    if not shape and not factor:
        raise ValueError('Either shape or factor needs to be specified.')

    if extent is not None:
        if (len(extent) != 4):
            raise ValueError('extent needs to be an array-like object of '
                             'length four.  It should desribe the upper-left '
                             'and lower-right corners of '
                             'the desired resmple area in pixels space as '
                             '[ul_x, ul_y, lr_x, lr_y] and can describe '
                             'points outside the image extent, '
                             'i.e. [-1, -2.1, 500, 501].  The shape or '
                             'factor parameter must also be provided to '
                             'describe the size of the requested array.')

    if factor is not None:
        # Prep factor based on input type/format
        if isinstance(factor,(float,int)):
            factor = [factor,factor]

        # Check that factor values are valid
        for f in factor:
            if f >= 1:
                raise ValueError('Factor values should be less than one.')

    if shape is not None:
        if (shape[0] >= arr.shape[1]) | (shape[1] >= arr.shape[2]):
            raise ValueError('The requested downsample shape should be less '
                             'than the array passed in.')

    # Check other input parameters
    if method not in ['aggregate','nearest','max','min']:
        raise ValueError("The downsample method can be 'aggregate' or "
                         "'nearest'.")

    # Set x_steps and y_steps for the downsampling process below
    if shape is not None:
        x_start = 0
        x_stop = arr.shape[1] # no -1 to bracket for base 0 indexing
        x_num = shape[0]+1 # +1 to index on both sides of block.
        y_start = 0
        y_stop = arr.shape[2] # no -1 to bracket for base 0 indexing
        y_num = shape[1]+1 # +1 to index on both sides of block.

    if factor is not None:
        x_start = 0
        x_stop = arr.shape[1]
        x_num = int(round(arr.shape[1]*factor[0]))+1
        y_start = 0
        y_stop = arr.shape[2]
        y_num = int(round(arr.shape[2]*factor[1]))+1

    if extent is not None:
        x_start = extent[0]
        x_stop = extent[2]
        y_start = extent[1]
        y_stop = extent[3]

    x_steps = np.linspace(x_start,x_stop,x_num)
    y_steps = np.linspace(y_start,y_stop,y_num)

    logger.debug('x_start, x_stop, x_num: %s, %s, %s' %
                                                (x_start, x_stop, x_num))
    logger.debug('y_start, y_stop, y_num: %s, %s, %s' %
                                                (y_start, y_stop, y_num))
    logger.debug('length of x_steps: %s' % len(x_steps))
    logger.debug('length of y_steps: %s' % len(y_steps))

    logger.debug('beggining of x_steps: %s ...' % x_steps[:3])
    logger.debug('end of x_steps: ... %s' % x_steps[-3:])
    logger.debug('beggining of y_steps: %s ...' % y_steps[:3])
    logger.debug('end of y_steps: ... %s' % y_steps[-3:])

    return downsample_to_grid(arr,x_steps,y_steps,no_data_value,method,source)

def downsample_to_grid(arr,x_steps,y_steps,no_data_value=None,
                                            method='aggregate',source=None):
    """
    Function to choose which execuatable to use based on efficiency and
    availability.

    Parameters
    ----------
    arr
    x_steps
    y_steps
    method

    Returns
    -------

    """

    global use_cv2
    global use_numba
    if source=='cv2':
        use_cv2 = True
        use_numba = False
    elif source=='numba':
        use_cv2 = False
        use_numba = True

    if no_data_value:
        arr = np.where(arr == no_data_value, 0, arr)

    # Find the appropriate algorithm to run with.
    if ((x_steps[0] == 0) and (x_steps[-1] == arr.shape[1]) and
        (y_steps[0] == 0) and (y_steps[-1] == arr.shape[2]) and
        use_cv2):
        # If the requested steps are exact subsets of the image array and
        # cv2 is available, use cv2 since it is fast.
        if method == 'aggregate':
            logger.debug('running aggregate with opencv::resize')
            type_cv_code = cv2.INTER_AREA
            out = run_opencv_resize(arr,x_steps,y_steps,type_cv_code)
        elif method == 'nearest':
            logger.debug('running nearest neighbor downsample with '
                         'opencv::resize')
            type_cv_code = cv2.INTER_NEAREST
            out = run_opencv_resize(arr,x_steps,y_steps,type_cv_code)
    elif use_numba:
        # If cv2 isn't available or the requested steps are from a grid
        # that doens't nicely overlap this image, use custom implementations
        # from below.
        if method == 'aggregate':
            logger.debug('running aggregate with custom numba function.')
            out = run_numba_aggregate(arr,x_steps,y_steps)
        elif method == 'nearest':
            logger.debug('running nearest neighbor downsample with '
                         'custom numba function.')
            out = run_numba_nearest(arr, x_steps, y_steps)
        elif method == 'max':
            logger.debug('running max with custom numba function.')
            out = run_numba_max(arr, x_steps, y_steps)
        elif method == 'min':
            logger.debug('running min with custom numba function.')
            out = run_numba_min(arr, x_steps, y_steps)
    else:
        raise ValueError('No downsampling routine available for the '
                         'requested parameters.  Either opencv or numba are '
                         'needed to run the downsample calculations.  '
                         'Additionally, if the requested grid does not '
                         'align with the edges of the image, you can not '
                         'use opencv and will need numba to run this '
                         'function.  You can always just use get_data() '
                         'and use an external resampling routine!')

    if no_data_value:
        return np.where(out == 0, no_data_value, out)
    else:
        return out

def run_opencv_resize(arr,x_steps,y_steps,type_cv_code):
    """TBD"""
    size = (len(x_steps)-1,len(y_steps)-1)
    out = np.empty([arr.shape[0]]+list(size))

    for b in xrange(out.shape[0]):
        out[b,:,:] = cv2.resize(arr[b,:,:],dsize=size[::-1],dst=None,
                                                interpolation=type_cv_code)
    return out

def run_numba_aggregate(arr,x_steps,y_steps):
    """TBD"""
    use_jit = False

    if use_jit:
        out = aggregate_numba_3d(arr,x_steps,y_steps)
    else:
        out = np.zeros((arr.shape[0],len(x_steps)-1,len(y_steps)-1),
                       dtype=arr.dtype)
        aggregate_guvec(arr,x_steps,y_steps,out)

    return out

def run_numba_nearest(arr,x_steps,y_steps):
    """TBD"""
    out = np.zeros((arr.shape[0],len(x_steps)-1,len(y_steps)-1),
                   dtype=arr.dtype)
    nearest_guvec(arr,x_steps,y_steps,out)

    return out

def run_numba_max(arr,x_steps,y_steps):
    """TBD"""
    out = np.zeros((arr.shape[0],len(x_steps)-1,len(y_steps)-1),
                   dtype=arr.dtype)
    max_guvec(arr,x_steps,y_steps,out)

    return out

def run_numba_min(arr,x_steps,y_steps):
    """TBD"""
    out = np.zeros((arr.shape[0],len(x_steps)-1,len(y_steps)-1),
                   dtype=arr.dtype)
    min_guvec(arr,x_steps,y_steps,out)

    return out


@jit(nopython=True)
def aggregate_pixel(arr,x_step,y_step):
    """Aggregation code for a single pixel"""

    # Set x/y to zero to mimic the setting in a loop
    # Assumes x_step and y_step in an array-type of length 2
    x = 0
    y = 0

    # initialize sum variable
    s = 0.0

    # sum center pixels
    left = int(ceil(x_step[x]))
    right = int(floor(x_step[x+1]))
    top = int(ceil(y_step[y]))
    bottom =  int(floor(y_step[y+1]))
    s += arr[left:right,top:bottom].sum()

    # Find edge weights
    wl = left - x_step[x]
    wr = x_step[x+1] - right
    wt = top - y_step[y]
    wb = y_step[y+1] - bottom
    # sum edges - left
    s += arr[left-1:left,top:bottom].sum() * wl
    # sum edges - right
    s += arr[right:right+1,top:bottom].sum() * wr
    # sum edges - top
    s += arr[left:right,top-1:top].sum() * wt
    # sum edges - bottom
    s += arr[left:right,bottom:bottom+1].sum() * wb

    # sum corners ...
    # ul
    s += arr[left-1:left,top-1:top].sum() * wl * wt
    # ur
    s += arr[right:right+1,top-1:top].sum() * wr * wt
    # ll
    s += arr[left-1:left,bottom:bottom+1].sum() * wl * wb
    # lr
    s += arr[right:right+1,bottom:bottom+1].sum() * wr * wb

    # calculate weight
    weight = (x_step[x+1]-x_step[x])*(y_step[y+1]-y_step[y])

    return s/float(weight)

@jit(nopython=True)
def nearest_pixel(arr,x_step,y_step):
    """Aggregation code for a single pixel"""

    # Set x/y to zero to mimic the setting in a loop
    # Assumes x_step and y_step in an array-type of length 2
    x = 0
    y = 0

    # initialize sum variable
    s = 0.0

    # nearest neighbor
    x_center = int(np.mean(x_step[x:x+2]))
    y_center = int(np.mean(x_step[y:y+2]))
    s += arr[x_center,y_center]

    return s

@jit(nopython=True)
def max_pixel(arr,x_step,y_step):
    """Aggregation code for a single pixel"""

    # Set x/y to zero to mimic the setting in a loop
    # Assumes x_step and y_step in an array-type of length 2
    x = 0
    y = 0

    # initialize sum variable
    s = 0.0

    # sum center pixels
    left = int(ceil(x_step[x]))
    right = int(floor(x_step[x+1]))
    top = int(ceil(y_step[y]))
    bottom =  int(floor(y_step[y+1]))
    s += arr[left-1:right+1,top-1:bottom+1].max()

    return s

@jit(nopython=True)
def min_pixel(arr,x_step,y_step):
    """Aggregation code for a single pixel"""

    # Set x/y to zero to mimic the setting in a loop
    # Assumes x_step and y_step in an array-type of length 2
    x = 0
    y = 0

    # initialize sum variable
    s = 0.0

    # sum center pixels
    left = int(ceil(x_step[x]))
    right = int(floor(x_step[x+1]))
    top = int(ceil(y_step[y]))
    bottom =  int(floor(y_step[y+1]))
    s += arr[left-1:right+1,top-1:bottom+1].min()

    return s


@jit(nopython=True)
def aggregate_numba_3d(arr,x_steps,y_steps):
    """TBD"""
    out = np.zeros((arr.shape[0],len(x_steps)-1,len(y_steps)-1),
                   dtype=arr.dtype)

    for b in xrange(out.shape[0]):
        for x in xrange(out.shape[1]):
            for y in xrange(out.shape[2]):
                out[b,x,y] = aggregate_pixel(arr[b,:,:],
                                             x_steps[x:x+2],
                                             y_steps[y:y+2])

    return out

# The types handled are the same in contstants.py DICT_GDAL_TO_NP
@guvectorize(['void(uint8[:,:],float64[:],float64[:],uint8[:,:])',
              'void(uint16[:,:],float64[:],float64[:],uint16[:,:])',
              'void(uint32[:,:],float64[:],float64[:],uint32[:,:])',
              'void(int16[:,:],float64[:],float64[:],int16[:,:])',
              'void(int32[:,:],float64[:],float64[:],int32[:,:])',
              'void(float32[:,:],float64[:],float64[:],float32[:,:])',
              'void(float64[:,:],float64[:],float64[:],float64[:,:])'],
            '(a,b),(c),(d),(m,n)',target='parallel',nopython=True)
def aggregate_guvec(arr, x_steps, y_steps, out):
    """TBD"""
    for x in xrange(out.shape[0]):
        for y in xrange(out.shape[1]):
            out[x,y] = aggregate_pixel(arr,x_steps[x:x+2],y_steps[y:y+2])

# The types handled are the same in contstants.py DICT_GDAL_TO_NP
@guvectorize(['void(uint8[:,:],float64[:],float64[:],uint8[:,:])',
              'void(uint16[:,:],float64[:],float64[:],uint16[:,:])',
              'void(uint32[:,:],float64[:],float64[:],uint32[:,:])',
              'void(int16[:,:],float64[:],float64[:],int16[:,:])',
              'void(int32[:,:],float64[:],float64[:],int32[:,:])',
              'void(float32[:,:],float64[:],float64[:],float32[:,:])',
              'void(float64[:,:],float64[:],float64[:],float64[:,:])'],
             '(a,b),(c),(d),(m,n)', target='parallel',
             nopython=True)
def nearest_guvec(arr, x_steps, y_steps, out):
    """TBD"""
    for x in xrange(out.shape[0]):
        for y in xrange(out.shape[1]):
            out[x, y] = nearest_pixel(arr,x_steps[x:x + 2],y_steps[y:y + 2])

# The types handled are the same in contstants.py DICT_GDAL_TO_NP
@guvectorize(['void(uint8[:,:],float64[:],float64[:],uint8[:,:])',
              'void(uint16[:,:],float64[:],float64[:],uint16[:,:])',
              'void(uint32[:,:],float64[:],float64[:],uint32[:,:])',
              'void(int16[:,:],float64[:],float64[:],int16[:,:])',
              'void(int32[:,:],float64[:],float64[:],int32[:,:])',
              'void(float32[:,:],float64[:],float64[:],float32[:,:])',
              'void(float64[:,:],float64[:],float64[:],float64[:,:])'],
             '(a,b),(c),(d),(m,n)', target='parallel',
             nopython=True)
def max_guvec(arr, x_steps, y_steps, out):
    """TBD"""
    for x in xrange(out.shape[0]):
        for y in xrange(out.shape[1]):
            out[x, y] = max_pixel(arr,x_steps[x:x + 2],y_steps[y:y + 2])


# The types handled are the same in contstants.py DICT_GDAL_TO_NP
@guvectorize(['void(uint8[:,:],float64[:],float64[:],uint8[:,:])',
              'void(uint16[:,:],float64[:],float64[:],uint16[:,:])',
              'void(uint32[:,:],float64[:],float64[:],uint32[:,:])',
              'void(int16[:,:],float64[:],float64[:],int16[:,:])',
              'void(int32[:,:],float64[:],float64[:],int32[:,:])',
              'void(float32[:,:],float64[:],float64[:],float32[:,:])',
              'void(float64[:,:],float64[:],float64[:],float64[:,:])'],
             '(a,b),(c),(d),(m,n)', target='parallel',
             nopython=True)
def min_guvec(arr, x_steps, y_steps, out):
    """TBD"""
    for x in xrange(out.shape[0]):
        for y in xrange(out.shape[1]):
            out[x, y] = min_pixel(arr,x_steps[x:x + 2],y_steps[y:y + 2])



# def aggregate_cython(float[:,:,:] arr, float[:] x_steps, float[:] y_steps)
#
#     cdef float[:,:,:] out = np.zeros((arr.shape[0],len(x_steps)-1,len(y_steps)-1))
#
#     for b in xrange(out.shape[0]):
#         for x in xrange(out.shape[1]):
#             for y in xrange(out.shape[2]):
#                 # initialize sum variable
#                 s = 0.0
#
#                 # sum center pixels
#                 left = int(ceil(x_steps[x]))
#                 right = int(floor(x_steps[x+1]))
#                 top = int(ceil(y_steps[y]))
#                 bottom =  int(floor(y_steps[y+1]))
#
#                 s += arr[b,left:right,top:bottom].sum()
#
#                 # Find edge weights
#                 wl = left - x_steps[x]
#                 wr = x_steps[x+1] - right
#                 wt = top - y_steps[y]
#                 wb = y_steps[y+1] - bottom
#                 # sum edges - left
#                 s += arr[b,left-1:left,top:bottom].sum() * wl
#                 # sum edges - right
#                 s += arr[b,right:right+1,top:bottom].sum() * wr
#                 # sum edges - top
#                 s += arr[b,left:right,top-1:top].sum() * wt
#                 # sum edges - bottom
#                 s += arr[b,left:right,bottom:bottom+1].sum() * wb
#
#                 # sum corners ...
#                 # ul
#                 s += arr[b,left-1:left,top-1:top].sum() * wl * wt
#                 # ur
#                 s += arr[b,right:right+1,top-1:top].sum() * wr * wt
#                 # ll
#                 s += arr[b,left-1:left,bottom:bottom+1].sum() * wl * wb
#                 # lr
#                 s += arr[b,right:right+1,bottom:bottom+1].sum() * wr * wb
#
#                 # calculate weight
#                 weight = (x_steps[x+1]-x_steps[x])*(y_steps[y+1]-y_steps[y])
#
#                 out[b,x,y] = s/float(weight)


def main():
    import time
    import dgsamples
    import geoio

    img_small = geoio.GeoImage(dgsamples.wv2_longmont_1k.ms)
    data_small = img_small.get_data()

    start = time.time()
    out_small_numba = downsample(data_small,shape=[300,300],source='numba')
    print('small numba:  %s' % (time.time()-start))

    start = time.time()
    out_small_cv2 = downsample(arr=data_small,shape=(300,300),source='cv2')
    print('small cv2:  %s' % (time.time()-start))

    print('Max diff is:  %s' % (out_small_numba-out_small_cv2).max())
    print('Min diff is:  %s' % (out_small_numba-out_small_cv2).min())

    # img_big = geoio.GeoImage('/mnt/panasas/nwl/data_HIRES/Gibraltar/VNIR/054312817010_01_P001_MUL/15FEB28112650-M2AS_R1C1-054312817010_01_P001.TIF')
    # data_big = img_big.get_data()
    #
    # start = time.time()
    # out_big_numba = downsample(data_big,shape=[1000,1000],source='numba')
    # print('big numba:  %s' % (time.time()-start))
    #
    # start = time.time()
    # out_big_cv2 = downsample(arr=data_big,shape=(1000,1000),source='cv2')
    # print('big cv2:  %s' % (time.time()-start))
    #
    # print('Max diff is:  %s' % (out_big_numba-out_big_cv2).max())
    # print('Min diff is:  %s' % (out_big_numba-out_big_cv2).min())

if __name__ == "__main__":
    main()