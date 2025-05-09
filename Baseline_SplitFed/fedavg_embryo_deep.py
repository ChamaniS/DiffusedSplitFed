import copy
import math
import os

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import torch

torch.cuda.empty_cache()
import albumentations as A
from albumentations.pytorch import ToTensorV2
from tqdm import tqdm
import torch.optim as optim
from utils_splitfed_deep_Huffman import (get_loaders, eval, get_loaders_test, test, inf)
import matplotlib as plt
import segmentation_models_pytorch as smp
from sklearn.metrics import jaccard_score
from options import args_parser
from agg.Fed_Avg import fedAvg
import numpy as np
import pandas as pd
import random
import time
from compressai.models import MeanScaleHyperprior
torch.backends.cudnn.deterministic = True
import torch.nn.functional as F
import sys


output_file = "/local-scratch/localhome/csj5/compressenv/Saved/compress/Fed_Avg/Outputs/output.txt"

# Redirect stdout to the text file
sys.stdout = open(output_file, "w")

CUDA_LAUNCH_BLOCKING = 1

# Hyperparameters
LEARNING_RATE = 0.0001
device = "cuda"
NUM_WORKERS = 1
SHUFFLE = False
NUM_CLASSES = 5
PIN_MEMORY = False
dataDir = "/local-scratch/localhome/csj5/Blastosyst_Data/"
baselineF = "/local-scratch/localhome/csj5/compressenv/Saved/baseline/"
compressedF = "/local-scratch/localhome/csj5/compressenv/Saved/compress/"

ifcompressed = 1

if (ifcompressed == 0):
    from clientmodel_FE2 import UNET_FE
    from clientmodel_BE2 import UNET_BE
    from servermodel2 import UNET_server

    save_F = baselineF
else:
    # models for training and validation
    from clientmodel_FE2 import UNET_FE
    from clientmodel_BE2 import UNET_BE
    from servermodel2 import UNET_server
    from compmodel_FE import COMP_NW_FE
    from compmodel_server import COMP_NW_SERVER

    # models for testing
    # from models.clientmodel_FE2_BQ import UNET_FET
    # from models.clientmodel_BE2 import UNET_BE
    # from models.servermodel2_BQ import UNET_serverT
    save_F = compressedF

# client 1
TRAIN_IMG_DIR_C1 = dataDir + "./client1/train_imgs/"
TRAIN_MASK_DIR_C1 = dataDir + "./client1/train_masks/"
VAL_IMG_DIR_C1 = dataDir + "./client1/val_imgs/"
VAL_MASK_DIR_C1 = dataDir + "./client1/val_masks/"

# client 2
TRAIN_IMG_DIR_C2 = dataDir + "./client2/train_imgs/"
TRAIN_MASK_DIR_C2 = dataDir + "./client2/train_masks/"
VAL_IMG_DIR_C2 = dataDir + "./client2/val_imgs/"
VAL_MASK_DIR_C2 = dataDir + "./client2/val_masks/"

# client 3
TRAIN_IMG_DIR_C3 = dataDir + "./client3/train_imgs/"
TRAIN_MASK_DIR_C3 = dataDir + "./client3/train_masks/"
VAL_IMG_DIR_C3 = dataDir + "./client3/val_imgs/"
VAL_MASK_DIR_C3 = dataDir + "./client3/val_masks/"

# client 4
TRAIN_IMG_DIR_C4 = dataDir + "./client4/train_imgs/"
TRAIN_MASK_DIR_C4 = dataDir + "./client4/train_masks/"
VAL_IMG_DIR_C4 = dataDir + "./client4/val_imgs/"
VAL_MASK_DIR_C4 = dataDir + "./client4/val_masks/"

# client 5
TRAIN_IMG_DIR_C5 = dataDir + "./client5/train_imgs/"
TRAIN_MASK_DIR_C5 = dataDir + "./client5/train_masks/"
VAL_IMG_DIR_C5 = dataDir + "./client5/val_imgs/"
VAL_MASK_DIR_C5 = dataDir + "./client5/val_masks/"

TEST_IMG_DIR = dataDir + "./test_imgs_new/"
TEST_MASK_DIR = dataDir + "./test_masks_new/"

def train(train_loader, local_model1, local_model2, local_model3,comp_model1, comp_model2, optimizer1, optimizer2, optimizer3,comp_op1,comp_op2, loss_fn):
    loop = tqdm(train_loader)
    train_running_loss = 0.0
    train_running_correct = 0.0
    train_iou_score = 0.0
    train_iou_score_class0 = 0.0
    train_iou_score_class1 = 0.0
    train_iou_score_class2 = 0.0
    train_iou_score_class3 = 0.0
    train_iou_score_class4 = 0.0
    train_running_totrateloss = 0.0
    train_running_rateloss1 = 0.0
    train_running_rateloss2 = 0.0
    train_running_mseloss1 = 0.0
    train_running_mseloss2 = 0.0
    train_running_totdiceloss = 0.0
    lambda1 =20000
    for batch_idx, (data, targets) in enumerate(loop):
        data = data.to(device)
        targets = targets.type(torch.LongTensor).to(device) 
        enc1, predictions1 = local_model1(data) 
        x_hat1, y_likelihoods1 = comp_model1(predictions1)
        # bitrate of the quantized latent
        N, C, H, W = predictions1.size()
        bpp_loss1 = torch.log(y_likelihoods1).sum() / (-math.log(2) *  N * C*H * W)
        mse_loss1 = F.mse_loss(predictions1, x_hat1)
        S1_totloss = bpp_loss1 + lambda1*(mse_loss1)

        predictions2 = local_model2(x_hat1)
        x_hat2, y_likelihoods2 = comp_model2(predictions2)
        # bitrate of the quantized latent
        N, C, H, W = predictions2.size()
        bpp_loss2 = torch.log(y_likelihoods2).sum() / (-math.log(2) * N * C*H * W)
        mse_loss2 = F.mse_loss(predictions2, x_hat2)
        S2_totloss = bpp_loss2 + lambda1*(mse_loss2)
        
        predictions3 = local_model3(enc1, x_hat2)
        dice_loss3 = loss_fn(predictions3, targets)
    
        loss = S1_totloss+S2_totloss+ lambda1*dice_loss3
        preds = torch.argmax(predictions3, dim=1)
        equals = preds == targets
        train_running_correct += torch.mean(equals.type(torch.FloatTensor)).item()
        train_running_loss += loss.item()
        train_running_mseloss1 += mse_loss1.item()
        train_running_mseloss2 += mse_loss2.item()
        train_running_rateloss1 += bpp_loss1.item()
        train_running_rateloss2 += bpp_loss2.item()

        train_running_totrateloss += bpp_loss1.item() + bpp_loss2.item()
        train_running_totdiceloss +=  dice_loss3.item()

        train_iou_score += jaccard_score(targets.cpu().flatten(), preds.cpu().flatten(), average='micro')
        iou_sklearn = jaccard_score(targets.cpu().flatten(), preds.cpu().flatten(), average=None)
        train_iou_score_class0 += iou_sklearn[0]
        train_iou_score_class1 += iou_sklearn[1]
        train_iou_score_class2 += iou_sklearn[2]
        train_iou_score_class3 += iou_sklearn[3]
        train_iou_score_class4 += iou_sklearn[4]
        loss.backward(retain_graph=True)
        optimizer3.step()
        optimizer3.zero_grad()
        mygrad3 = grads3
        #S2_totloss.backward(retain_graph=True)
        predictions2.backward(mygrad3,retain_graph=True)
        optimizer2.step()
        optimizer2.zero_grad()
        comp_model2.aux_loss().backward()
        comp_op2.step()
        comp_op2.zero_grad()
        mygrad2 = grads2  
        #S1_totloss.backward(retain_graph=True)
        predictions1.backward(mygrad2)
        optimizer1.step()
        optimizer1.zero_grad()
        comp_model1.aux_loss().backward()
        comp_op1.step()
        comp_op1.zero_grad()
        loop.set_postfix(loss=loss.item())
    epoch_loss = train_running_loss / len(train_loader.dataset)
    epoch_totrateloss = train_running_totrateloss / len(train_loader.dataset)
    epoch_rateloss1 = train_running_rateloss1 / len(train_loader.dataset)
    epoch_rateloss2 = train_running_rateloss2 / len(train_loader.dataset)
    epoch_mseloss1 = train_running_mseloss1 / len(train_loader.dataset)
    epoch_mseloss2 = train_running_mseloss2 / len(train_loader.dataset)
    epoch_totdiceloss = train_running_totdiceloss / len(train_loader.dataset)
    epoch_acc = 100. * (train_running_correct / len(train_loader.dataset))
    epoch_iou_class0 = (train_iou_score_class0 / len(train_loader.dataset))
    epoch_iou_class1 = (train_iou_score_class1 / len(train_loader.dataset))
    epoch_iou_class2 = (train_iou_score_class2 / len(train_loader.dataset))
    epoch_iou_class3 = (train_iou_score_class3 / len(train_loader.dataset))
    epoch_iou_class4 = (train_iou_score_class4 / len(train_loader.dataset))
    print("T epoch mse_loss1:", epoch_mseloss1)
    print("T epoch mse_loss2:", epoch_mseloss2)
    print("T epoch bpp_loss1:", epoch_rateloss1)
    print("T epoch bpp_loss2:", epoch_rateloss2)
    print("T epoch total bpp_loss:", epoch_totrateloss)
    print("T epoch total dice_loss:", epoch_totdiceloss)
    epoch_iou_withbackground = (epoch_iou_class0 + epoch_iou_class1 + epoch_iou_class2 + epoch_iou_class3 + epoch_iou_class4) / 5
    epoch_iou_nobackground = (epoch_iou_class1 + epoch_iou_class2 + epoch_iou_class3 + epoch_iou_class4) / 4
    return epoch_loss, epoch_acc, epoch_iou_withbackground, epoch_iou_nobackground, epoch_iou_class0, epoch_iou_class1, epoch_iou_class2, epoch_iou_class3, epoch_iou_class4


# 2. Main function
def main():
    args = args_parser()
    start_time = time.time()
    train_transform = A.Compose(
        [
            A.Resize(height=args.image_height, width=args.image_width),
            A.Normalize(
                mean=[0.0, 0.0, 0.0],
                std=[1.0, 1.0, 1.0],
                max_pixel_value=255.0,
            ),
            ToTensorV2(),
        ],
    )

    val_transforms = A.Compose(
        [
            A.Resize(height=args.image_height, width=args.image_width),
            A.Normalize(
                mean=[0.0, 0.0, 0.0],
                std=[1.0, 1.0, 1.0],
                max_pixel_value=255.0,
            ),
            ToTensorV2(),
        ],
    )

    train_loader_C1, val_loader_C1 = get_loaders(
        TRAIN_IMG_DIR_C1,
        TRAIN_MASK_DIR_C1,
        VAL_IMG_DIR_C1,
        VAL_MASK_DIR_C1,
        args.local_bs,
        train_transform,
        val_transforms,
        NUM_WORKERS,
        PIN_MEMORY

    )

    train_loader_C2, val_loader_C2 = get_loaders(
        TRAIN_IMG_DIR_C2,
        TRAIN_MASK_DIR_C2,
        VAL_IMG_DIR_C2,
        VAL_MASK_DIR_C2,
        args.local_bs,
        train_transform,
        val_transforms,
        NUM_WORKERS,
        PIN_MEMORY

    )

    train_loader_C3, val_loader_C3 = get_loaders(
        TRAIN_IMG_DIR_C3,
        TRAIN_MASK_DIR_C3,
        VAL_IMG_DIR_C3,
        VAL_MASK_DIR_C3,
        args.local_bs,
        train_transform,
        val_transforms,
        NUM_WORKERS,
        PIN_MEMORY
    )

    train_loader_C4, val_loader_C4 = get_loaders(
        TRAIN_IMG_DIR_C4,
        TRAIN_MASK_DIR_C4,
        VAL_IMG_DIR_C4,
        VAL_MASK_DIR_C4,
        args.local_bs,
        train_transform,
        val_transforms,
        NUM_WORKERS,
        PIN_MEMORY
    )

    train_loader_C5, val_loader_C5 = get_loaders(
        TRAIN_IMG_DIR_C5,
        TRAIN_MASK_DIR_C5,
        VAL_IMG_DIR_C5,
        VAL_MASK_DIR_C5,
        args.local_bs,
        train_transform,
        val_transforms,
        NUM_WORKERS,
        PIN_MEMORY
    )

    test_loader = get_loaders_test(
        TEST_IMG_DIR,
        TEST_MASK_DIR,
        train_transform
    )

    global_model1_fed = UNET_FE(in_channels=3).to(device)
    global_model2_fed = UNET_server(in_channels=32).to(device)
    global_model3_fed = UNET_BE(out_channels=NUM_CLASSES).to(device)
    #comp_nw1 = bmshj2018_factorized(quality=1,pretrained=True)
    #comp_nw2 = bmshj2018_factorized(quality=1,pretrained=True)

    comp_nw1 = COMP_NW_FE().to(device)
    comp_nw2 = COMP_NW_SERVER().to(device)

    #comp_nw1 = MeanScaleHyperprior(N=32,M=32).to(device)
    #comp_nw2 = MeanScaleHyperprior(N=64, M=64).to(device)

    # global round
    w_locals_model1, w_locals_model2, w_locals_model3 = [], [], []
    C1time, C2time, C3time, C4time, C5time = 0, 0, 0, 0, 0
    client1_train_acc, client1_train_loss, client1_train_withbackiou, client1_train_nobackiou, client1_val_acc, client1_val_loss, client1_val_withbackiou, client1_val_nobackiou, client1_g_val_acc, client1_g_val_loss, client1_g_val_iouwithback, client1_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [], []
    client2_train_acc, client2_train_loss, client2_train_withbackiou, client2_train_nobackiou, client2_val_acc, client2_val_loss, client2_val_withbackiou, client2_val_nobackiou, client2_g_val_acc, client2_g_val_loss, client2_g_val_iouwithback, client2_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [], []
    client3_train_acc, client3_train_loss, client3_train_withbackiou, client3_train_nobackiou, client3_val_acc, client3_val_loss, client3_val_withbackiou, client3_val_nobackiou, client3_g_val_acc, client3_g_val_loss, client3_g_val_iouwithback, client3_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [], []
    client4_train_acc, client4_train_loss, client4_train_withbackiou, client4_train_nobackiou, client4_val_acc, client4_val_loss, client4_val_withbackiou, client4_val_nobackiou, client4_g_val_acc, client4_g_val_loss, client4_g_val_iouwithback, client4_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [], []
    client5_train_acc, client5_train_loss, client5_train_withbackiou, client5_train_nobackiou, client5_val_acc, client5_val_loss, client5_val_withbackiou, client5_val_nobackiou, client5_g_val_acc, client5_g_val_loss, client5_g_val_iouwithback, client5_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [], []
    test_Acc, test_Iou_withback, test_Iou_noback, test_Loss = [], [], [], []

    client1_train_totrl, client2_train_totrl, client3_train_totrl, client4_train_totrl, client5_train_totrl = [], [], [], [], []
    client1_val_totrl, client2_val_totrl, client3_val_totrl, client4_val_totrl, client5_val_totrl = [], [], [], [], []
    client1_train_rl1, client2_train_rl1, client3_train_rl1, client4_train_rl1, client5_train_rl1 = [], [], [], [], []
    client1_train_rl2, client2_train_rl2, client3_train_rl2, client4_train_rl2, client5_train_rl2 = [], [], [], [], []
    client1_val_rl1, client2_val_rl1, client3_val_rl1, client4_val_rl1, client5_val_rl1 = [], [], [], [], []
    client1_val_rl2, client2_val_rl2, client3_val_rl2, client4_val_rl2, client5_val_rl2 = [], [], [], [], []
    client1_g_val_totrl, client2_g_val_totrl, client3_g_val_totrl, client4_g_val_totrl, client5_g_val_totrl = [], [], [], [], []
    client1_g_val_rl1, client2_g_val_rl1, client3_g_val_rl1, client4_g_val_rl1, client5_g_val_rl1 = [], [], [], [], []
    client1_g_val_rl2, client2_g_val_rl2, client3_g_val_rl2, client4_g_val_rl2, client5_g_val_rl2 = [], [], [], [], []
    test_t_qerr, test_qerr1, test_qerr2 = [], [], []

    least_lossg = 100000000;
    for com_round in (range(args.rounds)):
        local_weights1, local_weights2, local_weights3 = [], [], []
        least_lossC1, least_lossC2, least_lossC3, least_lossC4, least_lossC5 = 100000000, 100000000, 100000000, 100000000, 100000000;
        
        # Getting global model params
        round_idx = com_round + 1

        # --------------------------------------LOCAL TRAINING & VALIDATING---------------------------------------------------------------------------
        print(f'\n | Global Training Round : {round_idx} |\n')
        m = max(int(args.frac * args.num_users), 1)
        idxs_users = np.random.choice(range(args.num_users), m, replace=False)
        for idx in idxs_users:
            local_model1 = copy.deepcopy(global_model1_fed)
            local_model2 = copy.deepcopy(global_model2_fed)
            local_model3 = copy.deepcopy(global_model3_fed)
            comp_model1 = copy.deepcopy(comp_nw1)
            comp_model2 = copy.deepcopy(comp_nw2)

            loss_fn = smp.losses.DiceLoss(smp.losses.MULTICLASS_MODE, from_logits=True)
            optimizer1 = optim.Adam(local_model1.parameters(), lr=LEARNING_RATE)
            optimizer2 = optim.Adam(local_model2.parameters(), lr=LEARNING_RATE)
            optimizer3 = optim.Adam(local_model3.parameters(), lr=LEARNING_RATE)
            #aux_parameters1 = set(p for n, p in comp_model1.named_parameters() if n.endswith(".quantiles"))
            #aux_parameters2 = set(p for n, p in comp_model1.named_parameters() if n.endswith(".quantiles"))
            comp_op1 = optim.Adam(comp_model1.parameters(), lr=LEARNING_RATE)
            comp_op2 = optim.Adam(comp_model2.parameters(), lr=LEARNING_RATE)

            # Backward hook for modelclientBE
            grads3 = 0

            def grad_hook1(model, grad_input, grad_output):
                global grads3
                grads3 = grad_input[0].clone().detach()

            local_model3.upconv1.register_full_backward_hook(grad_hook1)

            # Backward hook for modelserver
            grads2 = 0

            def grad_hook2(model, grad_input, grad_output):
                global grads2
                grads2 = grad_input[0].clone().detach()

            local_model2.encoder2.register_full_backward_hook(grad_hook2)

            cl_idx = idx + 1
            print("Selected client:", cl_idx)
            if cl_idx == 1:
                train_loader = train_loader_C1
                val_loader = val_loader_C1
                folder = save_F + "./Fed_Avg/Saved/local_models/client1"
            elif cl_idx == 2:
                train_loader = train_loader_C2
                val_loader = val_loader_C2
                folder = save_F + "./Fed_Avg/Saved/local_models/client2"
            elif cl_idx == 3:
                train_loader = train_loader_C3
                val_loader = val_loader_C3
                folder = save_F + "./Fed_Avg/Saved/local_models/client3"
            elif cl_idx == 4:
                train_loader = train_loader_C4
                val_loader = val_loader_C4
                folder = save_F + "./Fed_Avg/Saved/local_models/client4"
            elif cl_idx == 5:
                train_loader = train_loader_C5
                val_loader = val_loader_C5
                folder = save_F + "./Fed_Avg/Saved/local_models/client5"

                # local epoch
            for epoch in range(args.local_ep):
                print(f"[INFO]: Epoch {epoch + 1} of {args.local_ep}")
                print("Client", cl_idx, " training.........")
                if cl_idx == 1:  # C1---------------------------------------------------------------C1 local training & validation--------------------------------------------------------------------------------------------------------------------
                    start_timec1 = time.time()
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1, trainepoch_iou_class2, trainepoch_iou_class3, trainepoch_iou_class4= train(
                        train_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, optimizer1,optimizer2, optimizer3, comp_op1, comp_op2, loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1, valepoch_iou_class2, valepoch_iou_class3, valepoch_iou_class4 = eval(
                        val_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, loss_fn, folder)
                    client1_train_acc.append(train_epoch_acc)
                    client1_train_loss.append(train_epoch_loss)
                    client1_train_withbackiou.append(trainepoch_iou_withbackground)
                    client1_train_nobackiou.append(trainepoch_iou_nobackground)
                    client1_val_acc.append(val_epoch_acc)
                    client1_val_loss.append(val_epoch_loss)
                    client1_val_withbackiou.append(valepoch_iou_withbackground)
                    client1_val_nobackiou.append(valepoch_iou_nobackground)
                    if least_lossC1 > val_epoch_loss:
                        least_lossC1 = val_epoch_loss
                        torch.save(local_model1.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C1M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C1M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C1M3_localcheckpoint.pth')
                        print('C1localmodel saved')
                    end_timec1 = time.time()
                    c1t = end_timec1 - start_timec1
                    C1time = C1time + c1t
                    print("C1 cumulative time:", C1time)

                if cl_idx == 2:  # C2--------------------------------------------------------------C2 local training & validation--------------------------------------------------------------------------------------------------------------------
                    start_timec2 = time.time()
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1, trainepoch_iou_class2, trainepoch_iou_class3, trainepoch_iou_class4= train(
                        train_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, optimizer1,optimizer2, optimizer3, comp_op1, comp_op2, loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1, valepoch_iou_class2, valepoch_iou_class3, valepoch_iou_class4 = eval(
                        val_loader, local_model1, local_model2, local_model3,  comp_model1, comp_model2, loss_fn, folder)
                    client2_train_acc.append(train_epoch_acc)
                    client2_train_loss.append(train_epoch_loss)
                    client2_train_withbackiou.append(trainepoch_iou_withbackground)
                    client2_train_nobackiou.append(trainepoch_iou_nobackground)
                    client2_val_acc.append(val_epoch_acc)
                    client2_val_loss.append(val_epoch_loss)
                    client2_val_withbackiou.append(valepoch_iou_withbackground)
                    client2_val_nobackiou.append(valepoch_iou_nobackground)
                    if least_lossC2 > val_epoch_loss:
                        least_lossC2 = val_epoch_loss
                        torch.save(local_model1.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C2M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C2M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C2M3_localcheckpoint.pth')
                        print('C2localmodel saved')
                    end_timec2 = time.time()
                    c2t = end_timec2 - start_timec2
                    C2time = C2time + c2t
                    print("C2 cumulative time:", C2time)

                if cl_idx == 3:  # C3--------------------------------------------------------------C3 local training & validation-----------------------------------------------------------------------------------------------------------
                    start_timec3 = time.time()
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1, trainepoch_iou_class2, trainepoch_iou_class3, trainepoch_iou_class4 = train(
                        train_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, optimizer1,optimizer2, optimizer3, comp_op1, comp_op2, loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1, valepoch_iou_class2, valepoch_iou_class3, valepoch_iou_class4= eval(
                        val_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, loss_fn, folder)
                    client3_train_acc.append(train_epoch_acc)
                    client3_train_loss.append(train_epoch_loss)
                    client3_train_withbackiou.append(trainepoch_iou_withbackground)
                    client3_train_nobackiou.append(trainepoch_iou_nobackground)
                    client3_val_acc.append(val_epoch_acc)
                    client3_val_loss.append(val_epoch_loss)
                    client3_val_withbackiou.append(valepoch_iou_withbackground)
                    client3_val_nobackiou.append(valepoch_iou_nobackground)
                    if least_lossC3 > val_epoch_loss:
                        least_lossC3 = val_epoch_loss
                        torch.save(local_model1.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C3M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C3M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C3M3_localcheckpoint.pth')
                        print('C3localmodel saved')
                    end_timec3 = time.time()
                    c3t = end_timec3 - start_timec3
                    C3time = C3time + c3t
                    print("C3 cumulative time:", C3time)

                if cl_idx == 4:  # C4--------------------------------------------------------------C4 local training & validation-----------------------------------------------------------------------------------------------------------
                    start_timec4 = time.time()
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1, trainepoch_iou_class2, trainepoch_iou_class3, trainepoch_iou_class4 = train(
                        train_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, optimizer1,optimizer2, optimizer3, comp_op1, comp_op2, loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1, valepoch_iou_class2, valepoch_iou_class3, valepoch_iou_class4 = eval(
                        val_loader, local_model1, local_model2, local_model3,comp_model1, comp_model2,  loss_fn, folder)
                    client4_train_acc.append(train_epoch_acc)
                    client4_train_loss.append(train_epoch_loss)
                    client4_train_withbackiou.append(trainepoch_iou_withbackground)
                    client4_train_nobackiou.append(trainepoch_iou_nobackground)
                    client4_val_acc.append(val_epoch_acc)
                    client4_val_loss.append(val_epoch_loss)
                    client4_val_withbackiou.append(valepoch_iou_withbackground)
                    client4_val_nobackiou.append(valepoch_iou_nobackground)
                    if least_lossC4 > val_epoch_loss:
                        least_lossC4 = val_epoch_loss
                        torch.save(local_model1.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C4M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C4M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C4M3_localcheckpoint.pth')
                        print('C4localmodel saved')
                    end_timec4 = time.time()
                    c4t = end_timec4 - start_timec4
                    C4time = C4time + c4t
                    print("C4 cumulative time:", C4time)

                if cl_idx == 5:  # C5--------------------------------------------------------------C5 local training & validation-----------------------------------------------------------------------------------------------------------
                    start_timec5 = time.time()
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1, trainepoch_iou_class2, trainepoch_iou_class3, trainepoch_iou_class4= train(
                        train_loader, local_model1, local_model2, local_model3, comp_model1, comp_model2, optimizer1,optimizer2, optimizer3, comp_op1, comp_op2, loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1, valepoch_iou_class2, valepoch_iou_class3, valepoch_iou_class4= eval(
                        val_loader, local_model1, local_model2, local_model3,comp_model1, comp_model2,  loss_fn, folder)
                    client5_train_acc.append(train_epoch_acc)
                    client5_train_loss.append(train_epoch_loss)
                    client5_train_withbackiou.append(trainepoch_iou_withbackground)
                    client5_train_nobackiou.append(trainepoch_iou_nobackground)
                    client5_val_acc.append(val_epoch_acc)
                    client5_val_loss.append(val_epoch_loss)
                    client5_val_withbackiou.append(valepoch_iou_withbackground)
                    client5_val_nobackiou.append(valepoch_iou_nobackground)
                    if least_lossC5 > val_epoch_loss:
                        least_lossC5 = val_epoch_loss
                        torch.save(local_model1.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C5M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C5M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),
                                   save_F + './Fed_Avg/Checkpoints/C5M3_localcheckpoint.pth')
                        print('C5localmodel saved')
                    end_timec5 = time.time()
                    c5t = end_timec5 - start_timec5
                    C5time = C5time + c5t
                    print("C5 cumulative time:", C5time)

                print(
                    f"Training loss: {train_epoch_loss:.3f}, Training accuracy: {train_epoch_acc:.3f},Training iou Score with background: {trainepoch_iou_withbackground:.3f},Training iou Score without background: {trainepoch_iou_nobackground:.3f}")
                print("\n Training IoUs Client:", cl_idx)
                print("T: Background:", trainepoch_iou_class0)
                print("T: ZP:", trainepoch_iou_class1)
                print("T: TE:", trainepoch_iou_class2)
                print("T: ICM:", trainepoch_iou_class3)
                print("T: Blastocoel:", trainepoch_iou_class4)

                print(
                    f"Validating loss: {val_epoch_loss:.3f}, Validating accuracy: {val_epoch_acc:.3f},Validating iou Score with background: {valepoch_iou_withbackground:.3f},Validating iou Score without background: {valepoch_iou_nobackground:.3f}")
                print("\n Validating IoUs Client:", cl_idx)
                print("V: Background:", valepoch_iou_class0)
                print("V: ZP:", valepoch_iou_class1)
                print("V: TE:", valepoch_iou_class2)
                print("V: ICM:", valepoch_iou_class3)
                print("V: Blastocoel:", valepoch_iou_class4)


        C1M1localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C1M1_localcheckpoint.pth')
        C1M2localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C1M2_localcheckpoint.pth')
        C1M3localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C1M3_localcheckpoint.pth')
        C2M1localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C2M1_localcheckpoint.pth')
        C2M2localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C2M2_localcheckpoint.pth')
        C2M3localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C2M3_localcheckpoint.pth')
        C3M1localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C3M1_localcheckpoint.pth')
        C3M2localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C3M2_localcheckpoint.pth')
        C3M3localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C3M3_localcheckpoint.pth')
        C4M1localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C4M1_localcheckpoint.pth')
        C4M2localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C4M2_localcheckpoint.pth')
        C4M3localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C4M3_localcheckpoint.pth')
        C5M1localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C5M1_localcheckpoint.pth')
        C5M2localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C5M2_localcheckpoint.pth')
        C5M3localbest = torch.load(save_F + './Fed_Avg/Checkpoints/C5M3_localcheckpoint.pth')

        tot_loader = len(train_loader_C1) + len(train_loader_C2) + len(train_loader_C3) + len(train_loader_C4) + len(
            train_loader_C5)
        D1 = len(train_loader_C1) / tot_loader;
        D2 = len(train_loader_C2) / tot_loader;
        D3 = len(train_loader_C3) / tot_loader;
        D4 = len(train_loader_C4) / tot_loader;
        D5 = len(train_loader_C5) / tot_loader;

        # updated parameters
        C1M1localbest.update((x, y * D1) for x, y in C1M1localbest.items())
        C1M2localbest.update((x, y * D1) for x, y in C1M2localbest.items())
        C1M3localbest.update((x, y * D1) for x, y in C1M3localbest.items())
        C2M1localbest.update((x, y * D2) for x, y in C2M1localbest.items())
        C2M2localbest.update((x, y * D2) for x, y in C2M2localbest.items())
        C2M3localbest.update((x, y * D2) for x, y in C2M3localbest.items())
        C3M1localbest.update((x, y * D3) for x, y in C3M1localbest.items())
        C3M2localbest.update((x, y * D3) for x, y in C3M2localbest.items())
        C3M3localbest.update((x, y * D3) for x, y in C3M3localbest.items())
        C4M1localbest.update((x, y * D4) for x, y in C4M1localbest.items())
        C4M2localbest.update((x, y * D4) for x, y in C4M2localbest.items())
        C4M3localbest.update((x, y * D4) for x, y in C4M3localbest.items())
        C5M1localbest.update((x, y * D5) for x, y in C5M1localbest.items())
        C5M2localbest.update((x, y * D5) for x, y in C5M2localbest.items())
        C5M3localbest.update((x, y * D5) for x, y in C5M3localbest.items())

        # Model1Averaging
        M1dict = [C1M1localbest, C2M1localbest, C3M1localbest, C4M1localbest, C5M1localbest]
        M2dict = [C1M2localbest, C2M2localbest, C3M2localbest, C4M2localbest, C5M2localbest]
        M3dict = [C1M3localbest, C2M3localbest, C3M3localbest, C4M3localbest, C5M3localbest]
        local_weights1.extend(M1dict)
        local_weights2.extend(M2dict)
        local_weights3.extend(M3dict)

        # averaging parameters
        global_fed_weights1 = fedAvg(local_weights1)
        global_fed_weights2 = fedAvg(local_weights2)
        global_fed_weights3 = fedAvg(local_weights3)

        # load the new parameters - FedAvg
        global_model1_fed.load_state_dict(global_fed_weights1)
        global_model2_fed.load_state_dict(global_fed_weights2)
        global_model3_fed.load_state_dict(global_fed_weights3)

        print("Weights averaged, loaded new weights")

        # ------------------------------------------VALIDATING USING THE GLOBAL MODEL-----------------------------------------------------------------------
        # Validating using the global model
        m1 = max(int(args.frac * args.num_users), 1)
        idxs_users1 = np.random.choice(range(args.num_users), m1, replace=False)
        for idx in idxs_users1:

            cl_idx = idx + 1
            print("Selected client:", cl_idx)
            if cl_idx == 1:
                val_loader = val_loader_C1
                folder = save_F + "./Fed_Avg/Saved/global_model/val/client1"
            elif cl_idx == 2:
                val_loader = val_loader_C2
                folder = save_F + "/Fed_Avg/Saved/global_model/val/client2"
            elif cl_idx == 3:
                val_loader = val_loader_C3
                folder = save_F + "/Fed_Avg/Saved/global_model/val/client3"
            elif cl_idx == 4:
                val_loader = val_loader_C4
                folder = save_F + "/Fed_Avg/Saved/global_model/val/client4"
            elif cl_idx == 5:
                val_loader = val_loader_C5
                folder = save_F + "/Fed_Avg/Saved/global_model/val/client5"

            best_epoch = 0
            for epoch in range(args.val_global_ep):
                print(f"[INFO]: Epoch {epoch + 1} of {args.val_global_ep}")
                print("Client", cl_idx, " validating.........")
                if cl_idx == 1:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1, g_valepoch_iou_class2, g_valepoch_iou_class3, g_valepoch_iou_class4 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed,comp_model1, comp_model2,  loss_fn, folder)
                    client1_g_val_acc.append(g_val_epoch_acc)
                    client1_g_val_loss.append(g_val_epoch_loss)
                    client1_g_val_iouwithback.append(g_val_epoch_iouwithback)
                if cl_idx == 2:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1, g_valepoch_iou_class2, g_valepoch_iou_class3, g_valepoch_iou_class4 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, comp_model1, comp_model2, loss_fn, folder)
                    client2_g_val_acc.append(g_val_epoch_acc)
                    client2_g_val_loss.append(g_val_epoch_loss)
                    client2_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client2_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 3:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1, g_valepoch_iou_class2, g_valepoch_iou_class3, g_valepoch_iou_class4 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed,comp_model1, comp_model2,  loss_fn, folder)
                    client3_g_val_acc.append(g_val_epoch_acc)
                    client3_g_val_loss.append(g_val_epoch_loss)
                    client3_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client3_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 4:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1, g_valepoch_iou_class2, g_valepoch_iou_class3, g_valepoch_iou_class4 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, comp_model1, comp_model2, loss_fn, folder)
                    client4_g_val_acc.append(g_val_epoch_acc)
                    client4_g_val_loss.append(g_val_epoch_loss)
                    client4_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client4_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 5:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1, g_valepoch_iou_class2, g_valepoch_iou_class3, g_valepoch_iou_class4 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, comp_model1, comp_model2, loss_fn, folder)
                    client5_g_val_acc.append(g_val_epoch_acc)
                    client5_g_val_loss.append(g_val_epoch_loss)
                    client5_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client5_g_val_iounoback.append(g_val_epoch_iounoback)

                print(
                    f"Global Validating loss: {g_val_epoch_loss:.3f}, Global Validating accuracy: {g_val_epoch_acc:.3f},Global Validating iou Score with background: {g_val_epoch_iouwithback:.3f},Global Validating iou Score without background: {g_val_epoch_iounoback:.3f}")
                print("\n Global Validating IoUs Client:", cl_idx)
                print("GV: Background:", g_valepoch_iou_class0)
                print("GV: ZP:", g_valepoch_iou_class1)
                print("GV: TE:", g_valepoch_iou_class2)
                print("GV: ICM:", g_valepoch_iou_class3)
                print("GV: Blastocoel:", g_valepoch_iou_class4)

        tot_gloss = client1_g_val_loss[-1] + client2_g_val_loss[-1] + client3_g_val_loss[-1] + client4_g_val_loss[-1] + \
                    client5_g_val_loss[-1]
        avg_g_val_loss = tot_gloss / 5;

        if least_lossg > avg_g_val_loss:
            least_lossg = avg_g_val_loss
            best_epoch = epoch
            torch.save(global_model1_fed.state_dict(),
                       save_F + './Fed_Avg/Checkpoints/M1_globalcheckpoint.pth')
            torch.save(global_model2_fed.state_dict(),
                       save_F + './Fed_Avg/Checkpoints/M2_globalcheckpoint.pth')
            torch.save(global_model3_fed.state_dict(),
                       save_F + './Fed_Avg/Checkpoints/M3_globalcheckpoint.pth')
            print('Global best model saved')
            print('-' * 50)

        # ------------------------------------------TESTING USING THE GLOBAL MODEL-----------------------------------------------------------------------

        test_folder = save_F + "/Fed_Avg/testingsaved"
        
        M1_test = copy.deepcopy(global_model1_fed)
        M2_test = copy.deepcopy(global_model2_fed)
        M3_test = copy.deepcopy(global_model3_fed)

        M1_test.load_state_dict(torch.load(save_F + './Fed_Avg/Checkpoints/M1_globalcheckpoint.pth'))
        M2_test.load_state_dict(torch.load(save_F + './Fed_Avg/Checkpoints/M2_globalcheckpoint.pth'))
        M3_test.load_state_dict(torch.load(save_F + './Fed_Avg/Checkpoints/M3_globalcheckpoint.pth'))
        
        for epoch in range(args.val_global_ep):
            print("Global testing.........")
            test_epoch_loss, test_epoch_acc, test_epoch_accwithback, test_epoch_accnoback = test(
                test_loader,
                M1_test,
                M2_test,
                M3_test,comp_model1, comp_model2,
                loss_fn, test_folder)
            print('\n')
            print(
                f"Testing loss: {test_epoch_loss:.3f}, Testing accuracy: {test_epoch_acc:.3f},Testing iou Score with background: {test_epoch_accwithback:.3f},Testing iou Score without background: {test_epoch_accnoback:.3f}")

            test_Acc.append(test_epoch_acc)
            test_Iou_withback.append(test_epoch_accwithback)
            test_Iou_noback.append(test_epoch_accnoback)
            test_Loss.append(test_epoch_loss)
            
            
          
        print('TRAINING COMPLETE')
        print('-' * 70)
        print('GLOBAL INFERENCE')
        # ---------------------------------------------------GLOBAL INFERENCE----------------------------------------------------------------------
        inf_folder = save_F + "/Fed_Avg/inferencesaved"
        
        for epoch in range(args.val_global_ep):
            print("Global inference.........")
            inf_epoch_loss, inf_epoch_acc, inf_epoch_accwithback, inf_epoch_accnoback = inf(test_loader,
                        global_model1_fed,
                        global_model2_fed,
                        global_model3_fed,comp_model1, comp_model2,
                        loss_fn, inf_folder)
            print('\n')
            print(f"Inference loss: {inf_epoch_loss:.3f}, Inference accuracy: {inf_epoch_acc},Inference iou Score with background: {inf_epoch_accwithback},Inference iou Score without background: {inf_epoch_accnoback}")
            print('\n Total Time: {0:0.4f}'.format(time.time() - start_time))
        
        print('INFERENCE COMPLETE')
        print('-' * 90)
        
        
        
        # -------------------------------------------------PLOTTING RESULTS-----------------------------------------------------------------------


        alltest_acc, alltest_iouwithback, alltest_iounoback, alltest_loss, alltest_qetot, alltest_qe1, alltest_qe2 = [], [], [], [], [], [], []
        alltest_acc.append(test_Acc)
        alltest_loss.append(test_Loss)
        alltest_iouwithback.append(test_Iou_withback)
        alltest_iounoback.append(test_Iou_noback)


        alltest_acc = pd.DataFrame(alltest_acc)
        alltest_loss = pd.DataFrame(alltest_loss)
        alltest_iouwithback = pd.DataFrame(alltest_iouwithback)
        alltest_iounoback = pd.DataFrame(alltest_iounoback)


        alltest_acc.to_csv(save_F + './Fed_Avg/Outputs/alltest_acc.csv')
        alltest_loss.to_csv(save_F + './Fed_Avg/Outputs/alltest_loss.csv')
        alltest_iouwithback.to_csv(save_F + './Fed_Avg/Outputs/alltest_iouwithback.csv')
        alltest_iounoback.to_csv(save_F + './Fed_Avg/Outputs/alltest_iouwithoutback.csv')


        # -------------------------------------------------------------------------------------
        
    sys.stdout.close()
    sys.stdout = sys.__stdout__
if __name__ == "__main__":
    main()

