
import os
import copy
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"
import torch
import albumentations as A
from albumentations.pytorch import ToTensorV2
from tqdm import tqdm
import torch.optim as optim
from clientmodel_FE2 import UNET_FE
from clientmodel_BE2 import UNET_BE
from servermodel2 import UNET_server
from utils_fetal_baseline import (get_loaders, eval, get_loaders_test, test)
import matplotlib.pyplot as plt
import segmentation_models_pytorch as smp
from sklearn.metrics import jaccard_score
import time
import pandas as pd
import argparse
from options import args_parser


from agg.Fed_Avg import fedAvg
import numpy as np

# Hyperparameters
LEARNING_RATE = 0.0001
device = "cuda"
NUM_WORKERS = 1
SHUFFLE= False
NUM_CLASSES = 3
PIN_MEMORY = False
IMAGE_HEIGHT = 224
IMAGE_WIDTH = 224
BATCH_SIZE =1
rounds = 10
num_users = 5
frac=1
local_ep = 1
val_global_ep = 1

DataF = "/local-scratch/localhome/csj5/fhpsaop_512_federated/federated/"
save_F = "/local-scratch/localhome/csj5/compressenv/Saved/baseline/Fed_Avg/"

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
torch.cuda.empty_cache()
ifcompressed = 1
from clientmodel_FE2 import UNET_FE
from clientmodel_BE2 import UNET_BE
from servermodel2 import UNET_server

TRAIN_IMG_DIR_C1 = DataF+"./client1/train_imgs/"
TRAIN_MASK_DIR_C1 = DataF+"./client1/train_masks/"
VAL_IMG_DIR_C1 = DataF+"./client1/val_imgs/"
VAL_MASK_DIR_C1 = DataF+"./client1/val_masks/"

#client 2
TRAIN_IMG_DIR_C2 = DataF+"./client2/train_imgs/"
TRAIN_MASK_DIR_C2 = DataF+"./client2/train_masks/"
VAL_IMG_DIR_C2 = DataF+"./client2/val_imgs/"
VAL_MASK_DIR_C2 = DataF+"./client2/val_masks/"

#client 3
TRAIN_IMG_DIR_C3 = DataF+"./client3/train_imgs/"
TRAIN_MASK_DIR_C3 = DataF+"./client3/train_masks/"
VAL_IMG_DIR_C3 = DataF+"./client3/val_imgs/"
VAL_MASK_DIR_C3 = DataF+"./client3/val_masks/"

#client 4
TRAIN_IMG_DIR_C4 = DataF+"./client4/train_imgs/"
TRAIN_MASK_DIR_C4 = DataF+"./client4/train_masks/"
VAL_IMG_DIR_C4 = DataF+"./client4/val_imgs/"
VAL_MASK_DIR_C4 = DataF+"./client4/val_masks/"

#client 5
TRAIN_IMG_DIR_C5 = DataF+"./client5/train_imgs/"
TRAIN_MASK_DIR_C5 = DataF+"./client5/train_masks/"
VAL_IMG_DIR_C5 = DataF+"./client5/val_imgs/"
VAL_MASK_DIR_C5 = DataF+"./client5/val_masks/"

TEST_IMG_DIR = DataF+"./test_imgs/"
TEST_MASK_DIR = DataF+"./test_masks/"

# 1. Train function
def train(train_loader, local_model1, local_model2, local_model3, optimizer1, optimizer2, optimizer3, loss_fn):
    grads3 = 0
    def grad_hook1(model, grad_input, grad_output):
        nonlocal grads3
        grads3 = grad_input[0].clone().detach()
    local_model3.decoder2_2.register_full_backward_hook(grad_hook1)

    grads2 = 0
    def grad_hook2(model, grad_input, grad_output):
        nonlocal grads2
        grads2 = grad_input[0].clone().detach()
    local_model2.encoder2_2.register_full_backward_hook(grad_hook2)

    #local_model1.train()
    #local_model2.train()
    #local_model3.train()
    loop = tqdm(train_loader)
    train_running_loss = 0.0
    train_running_correct = 0.0
    train_iou_score = 0.0
    train_iou_score_class0 = 0.0
    train_iou_score_class1 = 0.0
    train_iou_score_class2 = 0.0
    for batch_idx, (data, targets,_) in enumerate(loop):
        data = data.to(device)
        targets = targets.type(torch.LongTensor).to(device)
        enc1,predictions1 = local_model1(data)
        predictions2 = local_model2(predictions1)
        predictions3 = local_model3(enc1,predictions2)
        loss = loss_fn(predictions3, targets)
        preds = torch.argmax(predictions3, dim=1)
        equals = preds == targets
        train_running_correct += torch.mean(equals.type(torch.FloatTensor)).item()
        train_running_loss += loss.item()
        train_iou_score += jaccard_score(targets.cpu().flatten(), preds.cpu().flatten(), average='micro')
        iou_sklearn = jaccard_score(targets.cpu().flatten(), preds.cpu().flatten(), average=None)
        train_iou_score_class0 += iou_sklearn[0]
        train_iou_score_class1 += iou_sklearn[1]
        train_iou_score_class2 += iou_sklearn[2]
        loss.backward(retain_graph=True)
        optimizer3.step()
        optimizer3.zero_grad()
        mygrad3 = grads3
        predictions2.backward(mygrad3, retain_graph=True)
        optimizer2.step()
        optimizer2.zero_grad()
        mygrad2 = grads2
        predictions1.backward(mygrad2)
        optimizer1.step()
        optimizer1.zero_grad()
        loop.set_postfix(loss=loss.item())
    epoch_loss = train_running_loss / len(train_loader.dataset)
    epoch_acc = 100. * (train_running_correct / len(train_loader.dataset))
    epoch_iou_class0 = (train_iou_score_class0 / len(train_loader.dataset))
    epoch_iou_class1 = (train_iou_score_class1 / len(train_loader.dataset))
    epoch_iou_class2 = (train_iou_score_class2 / len(train_loader.dataset))
    epoch_iou_withbackground = (epoch_iou_class0 + epoch_iou_class1 +epoch_iou_class2) / 3
    epoch_iou_nobackground = (epoch_iou_class1 +epoch_iou_class2)/2
    return epoch_loss, epoch_acc, epoch_iou_withbackground, epoch_iou_nobackground, epoch_iou_class0, epoch_iou_class1 ,epoch_iou_class2


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

    test_transform = A.Compose(
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
        BATCH_SIZE,
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
        BATCH_SIZE,
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
        BATCH_SIZE,
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
        BATCH_SIZE,
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
        BATCH_SIZE,
        train_transform,
        val_transforms,
        NUM_WORKERS,
        PIN_MEMORY
    )


    test_loader = get_loaders_test(
        TEST_IMG_DIR,
        TEST_MASK_DIR,
        test_transform
    )

    global_model1_fed = UNET_FE(in_channels=3).to(device)
    global_model2_fed  = UNET_server(in_channels=32).to(device)
    global_model3_fed  = UNET_BE(out_channels=NUM_CLASSES).to(device)


    # global round
    w_locals_model1, w_locals_model2, w_locals_model3 = [], [], []
    client1_train_acc, client1_train_loss, client1_train_withbackiou,client1_train_nobackiou, client1_val_acc, client1_val_loss, client1_val_withbackiou,client1_val_nobackiou,client1_g_val_acc, client1_g_val_loss, client1_g_val_iouwithback,client1_g_val_iounoback = [], [], [], [], [], [], [], [], [],[], [],[]
    client2_train_acc, client2_train_loss, client2_train_withbackiou,client2_train_nobackiou, client2_val_acc, client2_val_loss, client2_val_withbackiou,client2_val_nobackiou,client2_g_val_acc, client2_g_val_loss, client2_g_val_iouwithback,client2_g_val_iounoback = [], [], [], [], [], [], [], [], [],[], [],[]
    client3_train_acc, client3_train_loss, client3_train_withbackiou,client3_train_nobackiou, client3_val_acc, client3_val_loss, client3_val_withbackiou,client3_val_nobackiou,client3_g_val_acc, client3_g_val_loss, client3_g_val_iouwithback,client3_g_val_iounoback = [], [], [], [], [], [], [], [], [],[], [],[]
    client4_train_acc, client4_train_loss, client4_train_withbackiou, client4_train_nobackiou, client4_val_acc, client4_val_loss, client4_val_withbackiou,client4_val_nobackiou, client4_g_val_acc, client4_g_val_loss, client4_g_val_iouwithback, client4_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [],[]
    client5_train_acc, client5_train_loss, client5_train_withbackiou, client5_train_nobackiou, client5_val_acc, client5_val_loss, client5_val_withbackiou,client5_val_nobackiou, client5_g_val_acc, client5_g_val_loss, client5_g_val_iouwithback, client5_g_val_iounoback = [], [], [], [], [], [], [], [], [], [], [],[]

    test_Acc, test_Iou_withback, test_Iou_noback, test_Loss = [], [], [], []
    least_lossg = 100000000;

    for com_round in (range(rounds)):
        local_weights1, local_weights2, local_weights3 = [], [], []
        least_lossC1, least_lossC2, least_lossC3, least_lossC4,least_lossC5 = 100000000, 100000000, 100000000, 100000000,100000000;

        # Getting global model params
        round_idx = com_round + 1

        # --------------------------------------LOCAL TRAINING & VALIDATING---------------------------------------------------------------------------
        print(f'\n | Global Training Round : {round_idx} |\n')
        m = max(int(frac * num_users), 1)
        idxs_users = np.random.choice(range(num_users), m, replace=False)
        for idx in idxs_users:
            local_model1 = copy.deepcopy(global_model1_fed)
            local_model2 = copy.deepcopy(global_model2_fed)
            local_model3 = copy.deepcopy(global_model3_fed)

            loss_fn = smp.losses.DiceLoss(smp.losses.MULTICLASS_MODE, from_logits=True)
            optimizer1 = optim.Adam(local_model1.parameters(), lr=LEARNING_RATE)
            optimizer2 = optim.Adam(local_model2.parameters(), lr=LEARNING_RATE)
            optimizer3 = optim.Adam(local_model3.parameters(), lr=LEARNING_RATE)

            cl_idx = idx + 1
            print("Selected client:", cl_idx)
            if cl_idx == 1:
                train_loader = train_loader_C1
                val_loader = val_loader_C1
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/local_models/client1"
            elif cl_idx == 2:
                train_loader = train_loader_C2
                val_loader = val_loader_C2
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/local_models/client2"
            elif cl_idx == 3:
                train_loader = train_loader_C3
                val_loader = val_loader_C3
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/local_models/client3"
            elif cl_idx == 4:
                train_loader = train_loader_C4
                val_loader = val_loader_C4
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/local_models/client4"
            elif cl_idx == 5:
                train_loader = train_loader_C5
                val_loader = val_loader_C5
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/local_models/client5"
                # local epoch
            for epoch in range(local_ep):
                print(f"[INFO]: Epoch {epoch + 1} of {local_ep}")
                print("Client", cl_idx, " training.........")
                if cl_idx == 1:  # C1---------------------------------------------------------------C1 local training & validation--------------------------------------------------------------------------------------------------------------------
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1,trainepoch_iou_class2 = train(
                        train_loader, local_model1, local_model2, local_model3, optimizer1, optimizer2, optimizer3,
                        loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1,valepoch_iou_class2 = eval(
                        val_loader, local_model1, local_model2, local_model3, loss_fn)
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
                        torch.save(local_model1.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C1M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C1M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C1M3_localcheckpoint.pth')
                        print('C1localmodel saved')


                if cl_idx == 2:  # C2--------------------------------------------------------------C2 local training & validation--------------------------------------------------------------------------------------------------------------------
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1,trainepoch_iou_class2= train(
                        train_loader, local_model1, local_model2, local_model3, optimizer1, optimizer2, optimizer3,
                        loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1,valepoch_iou_class2 = eval(
                        val_loader, local_model1, local_model2, local_model3, loss_fn)
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
                        torch.save(local_model1.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C2M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C2M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C2M3_localcheckpoint.pth')
                        print('C2localmodel saved')


                if cl_idx == 3:  # C3--------------------------------------------------------------C3 local training & validation-----------------------------------------------------------------------------------------------------------
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1,trainepoch_iou_class2 = train(
                        train_loader, local_model1, local_model2, local_model3, optimizer1, optimizer2, optimizer3,
                        loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1,valepoch_iou_class2= eval(
                        val_loader, local_model1, local_model2, local_model3, loss_fn)
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
                        torch.save(local_model1.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C3M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C3M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C3M3_localcheckpoint.pth')
                        print('C3localmodel saved')


                if cl_idx == 4:  # C4--------------------------------------------------------------C4 local training & validation-----------------------------------------------------------------------------------------------------------
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1,trainepoch_iou_class2 = train(
                        train_loader, local_model1, local_model2, local_model3, optimizer1, optimizer2, optimizer3,
                        loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1,valepoch_iou_class2 = eval(
                        val_loader, local_model1, local_model2, local_model3, loss_fn)
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
                        torch.save(local_model1.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C4M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C4M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C4M3_localcheckpoint.pth')
                        print('C4localmodel saved')

                if cl_idx == 5:  # C5--------------------------------------------------------------C5 local training & validation-----------------------------------------------------------------------------------------------------------
                    train_epoch_loss, train_epoch_acc, trainepoch_iou_withbackground, trainepoch_iou_nobackground, trainepoch_iou_class0, trainepoch_iou_class1,trainepoch_iou_class2 = train(
                        train_loader, local_model1, local_model2, local_model3, optimizer1, optimizer2, optimizer3,
                        loss_fn)
                    print("Client", cl_idx, "local validating.........")
                    val_epoch_loss, val_epoch_acc, valepoch_iou_withbackground, valepoch_iou_nobackground, valepoch_iou_class0, valepoch_iou_class1,valepoch_iou_class2 = eval(
                        val_loader, local_model1, local_model2, local_model3, loss_fn)
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
                        torch.save(local_model1.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C5M1_localcheckpoint.pth')
                        torch.save(local_model2.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C5M2_localcheckpoint.pth')
                        torch.save(local_model3.state_dict(),'./Saved/baseline/Fed_Avg/Checkpoints/C5M3_localcheckpoint.pth')
                        print('C5localmodel saved')



                print(
                    f"Training dice loss: {train_epoch_loss:.3f}, Training accuracy: {train_epoch_acc:.3f},Training iou Score with background: {trainepoch_iou_withbackground:.3f},Training iou Score without background: {trainepoch_iou_nobackground:.3f}")
                print("\n Training IoUs Client:", cl_idx)
                print("T: Background:", trainepoch_iou_class0)
                print("T: Area 1 :", trainepoch_iou_class1)
                print("T: Area 2 :", trainepoch_iou_class2)

                print(
                    f"Validating dice loss: {val_epoch_loss:.3f}, Validating accuracy: {val_epoch_acc:.3f},Validating iou Score with background: {valepoch_iou_withbackground:.3f},Validating iou Score without background: {valepoch_iou_nobackground:.3f}")
                print("\n Validating IoUs Client:", cl_idx)
                print("V: Background:", valepoch_iou_class0)
                print("V: Area 1:", valepoch_iou_class1)
                print("V: Area 2:", valepoch_iou_class2)

        C1M1localbest = torch.load(save_F+'./Checkpoints/C1M1_localcheckpoint.pth')
        C1M2localbest = torch.load(save_F+'./Checkpoints/C1M2_localcheckpoint.pth')
        C1M3localbest = torch.load(save_F+'./Checkpoints/C1M3_localcheckpoint.pth')
        C2M1localbest = torch.load(save_F+'./Checkpoints/C2M1_localcheckpoint.pth')
        C2M2localbest = torch.load(save_F+'./Checkpoints/C2M2_localcheckpoint.pth')
        C2M3localbest = torch.load(save_F+'./Checkpoints/C2M3_localcheckpoint.pth')
        C3M1localbest = torch.load(save_F+'./Checkpoints/C3M1_localcheckpoint.pth')
        C3M2localbest = torch.load(save_F+'./Checkpoints/C3M2_localcheckpoint.pth')
        C3M3localbest = torch.load(save_F+'./Checkpoints/C3M3_localcheckpoint.pth')
        C4M1localbest = torch.load(save_F+'./Checkpoints/C4M1_localcheckpoint.pth')
        C4M2localbest = torch.load(save_F+'./Checkpoints/C4M2_localcheckpoint.pth')
        C4M3localbest = torch.load(save_F+'./Checkpoints/C4M3_localcheckpoint.pth')
        C5M1localbest = torch.load(save_F+'./Checkpoints/C5M1_localcheckpoint.pth')
        C5M2localbest = torch.load(save_F+'./Checkpoints/C5M2_localcheckpoint.pth')
        C5M3localbest = torch.load(save_F+'./Checkpoints/C5M3_localcheckpoint.pth')

        tot_loader = len(train_loader_C1) + len(train_loader_C2) + len(train_loader_C3) + len(train_loader_C4) + len(train_loader_C5)
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
        M1dict =[C1M1localbest,C2M1localbest,C3M1localbest,C4M1localbest,C5M1localbest]
        M2dict = [C1M2localbest, C2M2localbest, C3M2localbest, C4M2localbest,C5M2localbest ]
        M3dict = [C1M3localbest, C2M3localbest, C3M3localbest, C4M3localbest,C5M3localbest    ]
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
        m1 = max(int(frac * num_users), 1)
        idxs_users1 = np.random.choice(range(num_users), m1, replace=False)
        for idx in idxs_users1:

            cl_idx = idx + 1
            print("Selected client:", cl_idx)
            if cl_idx == 1:
                val_loader = val_loader_C1
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/global_model/val/client1"
            elif cl_idx == 2:
                val_loader = val_loader_C2
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/global_model/val/client2"
            elif cl_idx == 3:
                val_loader = val_loader_C3
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/global_model/val/client3"
            elif cl_idx == 4:
                val_loader = val_loader_C4
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/global_model/val/client4"
            elif cl_idx == 5:
                val_loader = val_loader_C5
                #folder = parentF + "./Baseline/baseline/Fed_Avg/Saved/global_model/val/client5"

            best_epoch = 0
            for epoch in range(val_global_ep):
                print(f"[INFO]: Epoch {epoch + 1} of {val_global_ep}")
                print("Client", cl_idx, " validating.........")
                if cl_idx == 1:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1,g_valepoch_iou_class2 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, loss_fn)
                    client1_g_val_acc.append(g_val_epoch_acc)
                    client1_g_val_loss.append(g_val_epoch_loss)
                    client1_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client1_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 2:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1,g_valepoch_iou_class2  = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, loss_fn)
                    client2_g_val_acc.append(g_val_epoch_acc)
                    client2_g_val_loss.append(g_val_epoch_loss)
                    client2_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client2_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 3:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1,g_valepoch_iou_class2  = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, loss_fn)
                    client3_g_val_acc.append(g_val_epoch_acc)
                    client3_g_val_loss.append(g_val_epoch_loss)
                    client3_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client3_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 4:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1,g_valepoch_iou_class2 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, loss_fn)
                    client4_g_val_acc.append(g_val_epoch_acc)
                    client4_g_val_loss.append(g_val_epoch_loss)
                    client4_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client4_g_val_iounoback.append(g_val_epoch_iounoback)
                if cl_idx == 5:
                    g_val_epoch_loss, g_val_epoch_acc, g_val_epoch_iouwithback, g_val_epoch_iounoback, g_valepoch_iou_class0, g_valepoch_iou_class1,g_valepoch_iou_class2 = eval(
                        val_loader, global_model1_fed, global_model2_fed, global_model3_fed, loss_fn)
                    client5_g_val_acc.append(g_val_epoch_acc)
                    client5_g_val_loss.append(g_val_epoch_loss)
                    client5_g_val_iouwithback.append(g_val_epoch_iouwithback)
                    client5_g_val_iounoback.append(g_val_epoch_iounoback)


                print(
                    f"Global Validating dice loss: {g_val_epoch_loss:.3f}, Global Validating accuracy: {g_val_epoch_acc:.3f},Global Validating iou Score with background: {g_val_epoch_iouwithback:.3f},Global Validating iou Score without background: {g_val_epoch_iounoback:.3f}")
                print("\n Global Validating IoUs Client:", cl_idx)
                print("GV: Background:", g_valepoch_iou_class0)
                print("GV: Area 1:", g_valepoch_iou_class1)
                print("GV: Area 2:", g_valepoch_iou_class2)

        tot_gloss = client1_g_val_loss[0] + client2_g_val_loss[0] + client3_g_val_loss[0] + client4_g_val_loss[0] + client5_g_val_loss[0]
        avg_g_val_loss = tot_gloss / 5;

        if least_lossg > avg_g_val_loss:
            least_lossg = avg_g_val_loss
            best_epoch = epoch
            torch.save(global_model1_fed.state_dict(), save_F + './Checkpoints/M1_globalcheckpoint.pth')
            torch.save(global_model2_fed.state_dict(), save_F + './Checkpoints/M2_globalcheckpoint.pth')
            torch.save(global_model3_fed.state_dict(), save_F + './Checkpoints/M3_globalcheckpoint.pth')
            print('Global best model saved')
            print('-' * 50)

        # ------------------------------------------TESTING USING THE GLOBAL MODEL-----------------------------------------------------------------------

        test_folder = save_F + "/testingsaved"

        for epoch in range(val_global_ep):
            print("Global testing.........")
            test_epoch_loss, test_epoch_acc, test_epoch_accwithback, test_epoch_accnoback = test(test_loader,
                                                                                                 global_model1_fed,
                                                                                                 global_model2_fed,
                                                                                                 global_model3_fed,
                                                                                                 loss_fn, test_folder)
            print('\n')
            print(
                f"Testing dice loss: {test_epoch_loss:.3f}, Testing accuracy: {test_epoch_acc:.3f},Testing iou Score with background: {test_epoch_accwithback:.3f},Testing iou Score without background: {test_epoch_accnoback:.3f}")
            test_Acc.append(test_epoch_acc)
            test_Iou_withback.append(test_epoch_accwithback)
            test_Iou_noback.append(test_epoch_accnoback)
            test_Loss.append(test_epoch_loss)

        # Training time per each client--------------------------------------
        print("Each client's cumulative training time")

        # -------------------------------------------------PLOTTING RESULTS-----------------------------------------------------------------------

    print('TRAINING COMPLETE')
    print('\n Total Run Time: {0:0.4f}'.format(time.time() - start_time))


if __name__ == "__main__":
    main()
