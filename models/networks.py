from torch import nn
import torch
import torch.nn.functional as F

class Conv_3d_Block(nn.Module):
    def __init__(self, input_channels, output_channels, k_size, s,
            p=3, norm='none', activation="relu", pad_type="replicate") -> None:
        super(Conv_3d_Block, self).__init__()
        self.pad_type = pad_type
        self.padding = p
        if activation == "relu":
            self.activation = nn.ReLU(inplace=True)
        elif activation == "lrelu":
            self.activation = nn.LeakyReLU(0.2, inplace=True)
        elif activation == "prelu":
            self.activation = nn.PReLU()
        elif activation == "selu":
            self.activation = nn.SELU()
        elif activation == "tanh":
            self.activation = nn.Tanh()
        else:
            self.activation = None

        if norm == "bn":
            self.norm = nn.BatchNorm3d(output_channels)
        elif norm == "in":
            self.norm = nn.InstanceNorm3d(output_channels)
        else:
            self.norm = None

        self.conv = nn.Conv3d(in_channels=input_channels,
                out_channels=output_channels, kernel_size=k_size,
                stride=s)

    def forward(self, x):
        x = F.pad(x, [self.padding,]*6, self.pad_type)
        x = self.conv(x)
        if self.norm:
            x = self.norm(x)
        if self.activation:
            x = self.activation(x)
        return x

class ResidualBlock(nn.Module):
    def __init__(self, channels, norm="in", activation="relu",
            pad_type="replicate") -> None:
        super(ResidualBlock, self).__init__()
        layers = []
        layers += [Conv_3d_Block(channels, channels, 3, 1, 1, norm, activation,
            pad_type)]
        layers += [Conv_3d_Block(channels, channels, 3, 1, 1, norm,
            activation="none", pad_type=pad_type)]
        self.model = nn.Sequential(*layers)

    def forward(self, x):
        return x + self.model(x)

class ResidualBlocks(nn.Module):
    def __init__(self, n_blocks, channels, norm="in", activation="relu",
            pad_type="replicate") -> None:
        super(ResidualBlocks, self).__init__()
        layers = []
        for _ in range(n_blocks):
            layers += [ResidualBlock(channels, norm, activation, pad_type)]
        self.model = nn.Sequential(*layers)
    
    def forward(self, x):
        return self.model(x)

class StyleEncoder(nn.Module):
    def __init__(self, in_channels=14, n_downsample=4,
            style_dim=8, bottom_dim=32):
        super().__init__()
        self.model = []
        self.model += [Conv_3d_Block(in_channels, bottom_dim, 7, 1)]
        for _ in range(2):
            self.model += [Conv_3d_Block(bottom_dim, 2*bottom_dim, 4, 2, 1)]
            bottom_dim *= 2
        for _ in range(n_downsample - 2):
            self.model += [Conv_3d_Block(bottom_dim, bottom_dim, 4, 2, 1)]
        self.model += [nn.AdaptiveAvgPool3d(1)]
        self.model += [nn.Conv3d(bottom_dim, style_dim, 1, 1, 0)]
        self.final_model = nn.Sequential(*self.model)

    def forward(self, x):
        return self.final_model(x)

class ContentEncoder(nn.Module):
    def __init__(self, input_channels=14, n_downsample=2, bottom_dim=32, n_res=4):
        super(ContentEncoder, self).__init__()
        layers  = []
        layers += [Conv_3d_Block(input_channels, bottom_dim, 7, 1, norm="in")]
        for _ in range(n_downsample):
             layers += [Conv_3d_Block(bottom_dim, 2*bottom_dim, 4, 2, 1,
                 norm="in")]
             bottom_dim *= 2
        layers += [ResidualBlocks(n_res, bottom_dim)]
        self.model = nn.Sequential(*layers)

    def forward(self, x):
       return self.model(x)

class MultiDiscriminator(nn.Module):
    def __init__(self, params, in_channels=14) -> None:
        super().__init__()
        self.in_channels = in_channels
        self.n_layer = params['n_layer']
        self.bottom_dim = params['bottom_dim']
        self.norm = params['norm']
        self.activ = params['activ']
        self.num_scales = params['num_scales']
        self.pad_type = params['pad_type']

        self.models = nn.ModuleList()
        for _ in range(params["num_scales"]):
            self.models.append(self.discriminator_block())

        self.downsample = nn.AvgPool3d(kernel_size=3, stride=2, padding=1, count_include_pad=False)

    def discriminator_block(self):
        layers = []
        layers += [Conv_3d_Block(self.in_channels, self.bottom_dim, 4, 2, 1,
            norm="none", activation=self.activ, pad_type=self.pad_type)]
        channels = self.bottom_dim
        for _ in range(self.n_layer - 1):
            layers += [Conv_3d_Block(channels, 2*channels, 4, 2, 1,
                norm=self.norm, activation=self.activ, pad_type=self.pad_type)]
            channels *= 2
        layers += [nn.Conv3d(channels, 1, 3, padding=1)]
        return nn.Sequential(*layers)

    def forward(self, x):
        outputs = []
        for model in self.models:
            outputs.append(model(x))
            print(outputs[-1].shape, x.shape)
            x = self.downsample(x)
        return outputs

    def compute_loss(self, x, gt):
        """Computes the MSE between model output and scalar gt"""
        loss = sum([torch.mean((out - gt) ** 2) for out in self.forward(x)])
        return loss

class Decoder(nn.Module):
    def __init__(self, bottom_dim=32, n_res=3, n_upsample=2, style_dim=8,
            out_channels=14) -> None:
        super().__init__()
        layers = []
        channels = bottom_dim*(1<<n_upsample)
        layers += [ResidualBlocks(n_res, bottom_dim, norm="adain")]
        for _ in range(n_upsample):
            layers += [nn.Upsample(scale_factor=2), Conv_3d_Block(channels,
                channels//2, 5, 1, 2, norm="ln")]
            channels = channels//2
        layers += [Conv_3d_Block(channels, out_channels, 7, 1, activation="tanh")]
        self.model = nn.Sequential(*layers)
    
    def forward(self, x):
        return self.model(x)


if __name__ == "__main__":
    params = {"n_layer": 4, "activ": "lrelu", "num_scales": 1, "pad_type":
            "replicate", "norm": "in", "bottom_dim": 32}
    content_encoder = StyleEncoder()
    x = torch.randn(1, 14, 48, 48, 48)
    o = content_encoder(x)
    print(o.shape)
    D = MultiDiscriminator(params)
    rand_tensor = torch.randn(1, 14, 48, 48, 48)
    out = D(rand_tensor)
    for o in out:
        print(o.shape)