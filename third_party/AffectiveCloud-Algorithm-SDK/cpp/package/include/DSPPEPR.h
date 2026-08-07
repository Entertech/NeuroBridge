//
// Created by Enter M1 on 2023/8/28.
//

#ifndef AFFECTIVECPP_DSPPEPR_H
#define AFFECTIVECPP_DSPPEPR_H

#include "TypeDefine.h"
#include "Data.h"

namespace basic::dsp
{
    /// 呼吸波去噪
    /// \param inputWave 呼吸波
    /// \return 去噪后的呼吸波
    vectord rwWaveDenoise(const vectord& inputWave);

    /// 脉搏波信号质量检测
    /// \param bcgOutputWave 输出脉搏波波形
    /// \param oriWave 原始波形
    /// \param bcgAmpThr 脉搏波幅值阈值
    /// \param oriWaveRangeThr 原始波形跨度阈值
    /// \return 信号质量
    BCGQuality bcgQualityCal(const std::vector<double>& bcgOutputWave, const std::vector<double>& oriWave,
                             double bcgAmpThr, double oriWaveRangeThr);


    /// 呼吸波信号质量检测
    /// \param rwWave 呼吸波波形
    /// \param oriWave 原始波形
    /// \param noPfWave 无工频波形
    /// \param rwRangeThr 呼吸波跨度阈值
    /// \return
    RWQuality rwQualityCal(std::vector<double> rwWave, std::vector<double> oriWave, double rwRangeThr);
}

#endif //AFFECTIVECPP_DSPPEPR_H
