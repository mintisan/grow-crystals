import torch.nn as nn
import torch
import torch.optim as optim
import random
import numpy as np
import math
from src.utils.dataset import *
from src.utils.visualization import *

import sys
# import keyboard

from tqdm import tqdm


class customNNModule(nn.Module):
    def __init__(self):
        super(customNNModule, self).__init__()

    def train(self, param_dict: dict):

        num_epochs = param_dict['num_epochs']
        learning_rate = param_dict['learning_rate']
        train_dataloader = param_dict['train_dataloader']
        test_dataloader = param_dict['test_dataloader']
        device = param_dict['device']
        weight_decay = param_dict['weight_decay']
        video = False if 'video' not in param_dict else param_dict['video']

        verbose = True
        if 'verbose' in param_dict:
            verbose = param_dict['verbose']

        train_losses = []
        test_losses = []
        train_accuracies = []
        test_accuracies = []       

        best_loss = float('inf')
        patience = 100
        min_delta = 1e-4
        counter = 0

        optimizer = optim.AdamW(self.parameters(), lr=learning_rate, weight_decay=weight_decay)
        lamb_reg = 0.01 if 'lambda' not in param_dict else param_dict['lambda']
        for epoch in tqdm(range(num_epochs)):
            # if keyboard.is_pressed('ctrl+d'):
            #     print("Manual early stopping occurring.")
            #     break
            if video and epoch%10 == 0: # save every 10 epochs
                if hasattr(self.embedding, 'weight'):
                    embd = self.embedding.weight
                else:
                    embd = self.embedding.data
                visualize_embedding(embd, title=f"Epoch {epoch}", save_path=f"../video_imgs/{epoch}.png", dict_level = None, color_dict = True, adjust_overlapping_text = False)

            train_loss = 0
            train_correct = 0
            train_total = 0
            for batch_inputs, batch_targets in train_dataloader:
                batch_inputs = batch_inputs.to(device)
                batch_targets = batch_targets.type(torch.LongTensor).to(device)
                optimizer.zero_grad()
                logits = self.forward(batch_inputs)

#               class_counts = torch.bincount(batch_targets.squeeze(), minlength=self.vocab_size).double() + 1e-8
#               class_weights = 1 / class_counts.cuda()

                criterion = nn.CrossEntropyLoss()#weight=class_weights)
                
                loss = criterion(logits, batch_targets.squeeze())
                
                if hasattr(self.embedding, 'weight'):
                    total_loss = loss + lamb_reg * torch.mean(torch.sqrt(torch.mean(self.embedding.weight**2, dim=0)))
                else:
                    total_loss = loss + lamb_reg * torch.mean(torch.sqrt(torch.mean(self.embedding.data**2, dim=0)))
                
                total_loss.backward()
                optimizer.step()
                train_loss += loss.item()

                # Compute training accuracy
                _, predicted = torch.max(logits, 1)
                train_correct += (predicted == batch_targets).sum().item()
                train_total += batch_targets.size(0)

            test_loss = 0
            test_correct = 0
            test_total = 0

            with torch.no_grad():
                for batch_inputs, batch_targets in test_dataloader:
                    batch_inputs = batch_inputs.to(device)
                    batch_targets = batch_targets.type(torch.LongTensor).to(device)
                    logits = self.forward(batch_inputs)
                    criterion = nn.CrossEntropyLoss()
                    loss = criterion(logits, batch_targets.squeeze())
                    test_loss += loss.item()

                    # Compute test accuracy
                    _, predicted = torch.max(logits, 1)
                    test_correct += (predicted == batch_targets).sum().item()
                    test_total += batch_targets.size(0)

            if (epoch + 1) % 50 == 0 and verbose:
                print(f"Epoch {epoch + 1}/{num_epochs}, Train Loss: {train_loss / len(train_dataloader):.4f}, Train Acc: {train_correct / train_total:.4f}, Test Loss: {test_loss / len(test_dataloader):.4f}, Test Acc: {test_correct / test_total:.4f}")
                sys.stdout.flush()
            
            train_losses.append(train_loss / len(train_dataloader))
            test_losses.append(test_loss / len(test_dataloader))
            train_accuracies.append(train_correct / train_total)
            test_accuracies.append(test_correct / test_total)

            epoch_loss = train_loss / len(train_dataloader)
            # Check for convergence
            if best_loss - epoch_loss > min_delta:
                best_loss = epoch_loss
                counter = 0  # Reset counter if there's an improvement
            else:
                counter += 1  # Increment counter if no improvement

            '''
            if counter >= patience:
                print("Early stopping triggered!")
                break
            '''

        ret_dic = {}
        ret_dic['train_losses'] = train_losses
        ret_dic['test_losses'] = test_losses
        ret_dic['train_accuracies'] = train_accuracies
        ret_dic['test_accuracies'] = test_accuracies

        return ret_dic

    

class MLP(customNNModule):
    def __init__(self, shp, vocab_size, embd_dim, input_token=2, init_scale=1., unembd=False, weight_tied=False, seed=0):
        super(MLP, self).__init__()
        
        torch.manual_seed(seed)
        np.random.seed(seed)
        random.seed(seed)

        self.depth = len(shp) - 1
            
        linear_list = []
        for i in range(self.depth):
            linear_list.append(nn.Linear(shp[i], shp[i+1]))
        
        self.embedding = nn.Embedding(vocab_size, embd_dim)
        nn.init.normal_(self.embedding.weight, mean=0, std=1/np.sqrt(embd_dim))
#        self.embedding = torch.nn.Parameter(torch.normal(0,1/torch.tensor(embd_dim),size=(vocab_size, embd_dim))*init_scale)
        #self.embedding = torch.nn.Parameter(torch.normal(0,1,size=(vocab_size, embd_dim))*init_scale)
        self.linears = nn.ModuleList(linear_list)
        self.shp = shp
        
        assert shp[-1] == vocab_size
        assert shp[0] == input_token * embd_dim
        
        self.input_token = input_token
        self.embd_dim = embd_dim
        self.vocab_size = vocab_size
        self.unembd = unembd
        
        if unembd:
            assert shp[-2] == embd_dim
            if weight_tied:
                #self.linears[-1].weight = self.embedding
                self.embedding = self.linears[-1].weight

    def id2embd(self, data_id):
        assert data_id.shape[1] == self.input_token
        batch = data_id.shape[0]
        return self.embedding[data_id].reshape(batch,-1)
    
    def forward(self, x):
        x = self.id2embd(x)
#        print(torch.sqrt(torch.mean(x**2)))
        f = torch.nn.SiLU()
        for i in range(self.depth-1):
            x = self.linears[i](x)
            if i < self.depth - 2 or not self.unembd:
                x = f(x)
        x = self.linears[-1](x)
        return x
    
    def pred_logit(self, x):
        return self.forward(x)
    
    
class DistLayer(torch.nn.Linear):
    def __init__(self, in_features, out_features, n=1., eps=1e-4, bias=False):
        super(DistLayer, self).__init__(in_features, out_features, bias=bias)
        self.n = n
        self.eps = eps
        
    def forward(self, x, scale=False):
        # x: (B, N)
        # w: (V, N)
        # dist_sq: (B, V)
        n_embd = x.size(-1,)
        w = self.weight
        wx = torch.einsum('bn,vn->bv', x, w) # (B, V)
        ww = torch.norm(w, dim=-1)**2 # (V,)
        xx = torch.norm(x, dim=-1)**2 # (B,)

        dist_sq = ww[None,:] + xx[:,None] - 2 * wx + self.eps
        dist_sq = dist_sq / torch.min(dist_sq, dim=-1, keepdim = True)[0]
        return (dist_sq)**(-self.n)
    
class MLP_HS(customNNModule):
    def __init__(self, shp, vocab_size, embd_dim, input_token=2, init_scale=1., weight_tied=True, n=1., seed=0):
        super(MLP_HS, self).__init__()
        
        torch.manual_seed(seed)
        np.random.seed(seed)

        self.depth = len(shp) - 1
            
        linear_list = []
        for i in range(self.depth):
            if i < self.depth - 1:
                linear_list.append(nn.Linear(shp[i], shp[i+1]))
            else:
                linear_list.append(DistLayer(shp[i], shp[i+1], n=n))
        
        self.embedding = nn.Embedding(vocab_size, embd_dim)
        nn.init.normal_(self.embedding.weight, mean=0, std=1/np.sqrt(embd_dim)*init_scale)
        #self.embedding = torch.nn.Parameter(torch.normal(0,1/torch.tensor(embd_dim),size=(vocab_size, embd_dim))*init_scale)
#        self.embedding = torch.nn.Parameter(torch.normal(0,1,size=(vocab_size, embd_dim))*init_scale)
        self.linears = nn.ModuleList(linear_list)
        self.shp = shp
        
        assert shp[-1] == vocab_size
        assert shp[-2] == embd_dim
        assert shp[0] == input_token * embd_dim
        
        self.input_token = input_token
        self.embd_dim = embd_dim
        self.vocab_size = vocab_size
        
        self.weight_tied = weight_tied
        
        if weight_tied:
            self.embedding = self.linears[-1].weight
            
    def id2embd(self, data_id):
        assert data_id.shape[1] == self.input_token
        batch = data_id.shape[0]
        return self.embedding[data_id].reshape(batch,-1)

    def forward(self, x):
        x = self.id2embd(x)
        f = torch.nn.SiLU()
        for i in range(self.depth-1):
            x = self.linears[i](x)
            if i < self.depth - 2:
                x = f(x)
        x = self.linears[-1](x)

        prob_unnorm = x
        prob = prob_unnorm/torch.sum(prob_unnorm, dim=1, keepdim=True)
        logits = torch.log(prob)
        return logits
    
    def pred_logit(self, x):
        return self.forward(x)
    

# 2-Layer Transformer Model with Explicit Residual Connections
class ToyTransformer(customNNModule):
    def __init__(self, vocab_size, d_model, nhead, num_layers, seq_len = 16, init_scale=1.,use_dist_layer = False, seed=0, n_dist=1.):
        super(ToyTransformer, self).__init__()

        torch.manual_seed(seed)
        np.random.seed(seed)


        self.embedding = nn.Embedding(vocab_size, d_model)
        nn.init.normal_(self.embedding.weight, mean=0, std=1/np.sqrt(d_model)*init_scale)
        self.positional_encoding = nn.Parameter(torch.randn(seq_len, d_model))

        # Define transformer encoder layers
        self.layers = nn.ModuleList([
            nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, dim_feedforward=d_model*4, batch_first=True
            ) for _ in range(num_layers)
        ])
        self.use_dist_layer = use_dist_layer
        if use_dist_layer:
            self.dist = DistLayer(d_model, vocab_size, n=n_dist, eps=1e-4, bias=False)
        self.fc = nn.Linear(d_model, vocab_size)
        self.vocab_size = vocab_size

    def forward(self, x):
        embedded = self.embedding(x) + self.positional_encoding

        # Pass through transformer layers with residual connections
        x = embedded
        for layer in self.layers:
            x = layer(x) + x  # Explicit residual connection
            
        if self.use_dist_layer:
            x = x[:, -1]
            x = self.dist(x)
            prob = x/torch.sum(x, dim=1, keepdim=True)
            logits = torch.log(prob)
        else:
            logits = torch.einsum('bh,vh->bv', x[:, -1], self.embedding.weight)
#            logits = self.fc(x[:, -1])  # Only predict the last token
        return logits
    

def load_model_from_file(model_id, data_id, results_root = "results",data_size = 1000, train_ratio=0.8,seed=66,n_exp=None, embd_dim=16, device='cpu', trained_on_gpu=False, n_in_filename=False,):

    input_token=2

    if data_id == "lattice":
        dataset = parallelogram_dataset(p=5, dim=2, num=data_size, seed=seed, device=device)
        input_token = 3
    elif data_id == "greater":
        dataset = greater_than_dataset(p=30, num=data_size, seed=seed, device=device)
    elif data_id == "family_tree":
        dataset = family_tree_dataset_2(p=127, num=data_size, seed=seed, device=device)
    elif data_id == "equivalence":
        input_token = 1
        dataset = mod_classification_dataset(p=100, num=data_size, seed=seed, device=device)
    elif data_id == "circle":
        dataset = modular_addition_dataset(p=31, num=data_size, seed=seed, device=device)
    elif data_id=="permutation":
        dataset = permutation_group_dataset(p=4, num=data_size, seed=seed, device=device)
    else:
        raise ValueError(f"Unknown data_id: {data_id}")

    dataset = split_dataset(dataset, train_ratio=train_ratio, seed=seed)

    vocab_size = dataset['vocab_size']

    if model_id == "H_MLP":
        weight_tied = True
        hidden_size = 100
        shp = [input_token * embd_dim, hidden_size, embd_dim, vocab_size]
        model = MLP_HS(shp=shp, vocab_size=vocab_size, embd_dim=embd_dim, input_token=input_token, weight_tied=weight_tied, seed=seed, n=embd_dim, init_scale=1).to(device)
    elif model_id == "standard_MLP":
        unembd = True
        weight_tied = True
        hidden_size = 100
        shp = [input_token * embd_dim, hidden_size, embd_dim, vocab_size]
        model = MLP(shp=shp, vocab_size=vocab_size, embd_dim=embd_dim, input_token=input_token, unembd=unembd, weight_tied=weight_tied, seed=seed, init_scale=1).to(device)
    elif model_id == "H_transformer":
        model = ToyTransformer(vocab_size=vocab_size, d_model=embd_dim, nhead=2, num_layers=2, n_dist=embd_dim,seq_len=input_token, seed=seed, use_dist_layer=True, init_scale=1).to(device)
    elif model_id == "standard_transformer":
        model = ToyTransformer(vocab_size=vocab_size, d_model=embd_dim, nhead=2, num_layers=2, seq_len=input_token, seed=seed, use_dist_layer=False, init_scale=1).to(device)
    else:
        raise ValueError(f"Unknown model_id: {model_id}")
    
    if n_in_filename:
        load_path = f"../{results_root}/{seed}_permutation_{model_id}_{data_size}_{train_ratio}_{n_exp}.pt"
    else:
        load_path = f"../{results_root}/{seed}_permutation_{model_id}_{data_size}_{train_ratio}.pt"
    
    if trained_on_gpu:
        model.load_state_dict(torch.load(load_path), map_location=torch.device('cpu'))
    else:
        model.load_state_dict(torch.load(load_path))

    return model