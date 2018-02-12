from __future__ import print_function

import tensorflow as tf
import read_sunrgbd_data
from PIL import Image
import argparse

from UNet import unet
import time
import numpy as np

import horovod.tensorflow as hvd

headless = 'True'
img_width  = 320
img_height = 240

# Training settings
parser = argparse.ArgumentParser(description='plotting example')
parser.add_argument('--batch-size', type=int, default=20, metavar='N',
                    help='input batch size for training (default: 64)')
                    
args = parser.parse_args()

rows = np.int(np.ceil(np.sqrt(args.batch_size)))
cols = np.int(np.ceil(args.batch_size / rows))

hvd.init()


SUNRGBD_dataset = read_sunrgbd_data.dataset("SUNRGBD",
                                            "/se3netsproject/data/multijtdata/baxter_babbling_rarm_3.5hrs_Dec14_16/postprocessmotions/motion0",
                                            img_type='depth')

max_labels = 23

batch_size = 30
learning_rate = 1e-3
iter_num = 0

logs_path = '/tensorboard/tf-summary-logs/'
img_type = 'depth'


checkpoint_dir = '/tensorboard/checkpoints' if hvd.rank() == 0 else None

global_step = tf.train.get_or_create_global_step()

UNET = unet(batch_size, img_height, img_width, learning_rate, sess=None, num_classes=max_labels, is_training=True,
            img_type=img_type, use_horovod=True, global_step=global_step)

hooks = [
        # Horovod: BroadcastGlobalVariablesHook broadcasts initial variable states
        # from rank 0 to all other processes. This is necessary to ensure consistent
        # initialization of all workers when training is started with random weights
        # or restored from a checkpoint.
        hvd.BroadcastGlobalVariablesHook(0),
        tf.train.StopAtStepHook(last_step=600000 // hvd.size())
    ]

config = tf.ConfigProto()
config.gpu_options.allow_growth = True
config.gpu_options.visible_device_list = str(hvd.local_rank())

summary_writers = []

write_images_per_sec_files = False

with tf.train.MonitoredTrainingSession(config=config, hooks=hooks) as mon_sess:

    for i in range(0, hvd.size()):
        summary_writer = tf.summary.FileWriter(logs_path + 'hvd_rank_{:03d}'.format(i),
                                               graph=tf.get_default_graph())
        summary_writers.append(summary_writer)

    UNET.add_session(mon_sess)

    while not mon_sess.should_stop():

        # Run a training step synchronously.
        img, label = SUNRGBD_dataset.get_random_shuffle(batch_size)
        batch_labels = label

        label = np.reshape(label, [-1])

        if iter_num <= 10:
            UNET.set_learning_rate(learning_rate=1e-2)# * hvd.size())

        elif (iter_num > 10 and iter_num <= 500):
            UNET.set_learning_rate(learning_rate=1e-3)# * hvd.size())
        else:
            UNET.set_learning_rate(learning_rate=1e-4) #* hvd.size())

        batch_start = time.time()
        train_op, cost, pred, summary = UNET.train_batch(img, label)
        time_taken = time.time() - batch_start
        images_per_sec = batch_size / time_taken

        summary_writers[hvd.rank()].add_summary(summary, iter_num)

        print('iter = ', iter_num, 'hvd_rank = ', hvd.rank(), 'cost = ', cost, 'images/sec = ', images_per_sec, 'batch_size = ', batch_size)

        if write_images_per_sec_files:
            fileName = logs_path + 'time_gpus_{:03d}_gpuid_{:03d}_iter_{:03d}.txt'.format(hvd.size(), hvd.rank(), iter_num)

            with open(fileName,'w') as f:
                f.write(str(images_per_sec))

        iter_num = iter_num + 1
