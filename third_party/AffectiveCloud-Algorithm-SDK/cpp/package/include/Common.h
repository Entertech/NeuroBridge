#include "TypeDefine.h"
#include "NumCpp.hpp"
#include <math.h>

namespace basic
{
    namespace dsp
    {

                /**
         * @brief 通带频谱计算
         * 
         * @param wave 波形数据
         * @param fl 下限频率
         * @param fh 上限频率
         * @param fs 采样率
         * @param hammingOn 是否使用汉明窗
         * @param nFFT FFT点数
         * @return nc::NdArray<double> 通带频谱
         */
        nc::NdArray<double> bandSpectrumCal(nc::NdArray<double> wave, double fl, double fh, double fs, bool hammingOn=false, int nFFT = NAN);

        /**
         * 通用带通滤波器
         * @param wave  信号
         * @param fl    低频截止频率
         * @param fh    高频截止频率
         * @param fs    采样率
         * @param order 滤波器阶数
         * @return  滤波后信号
         */
        std::vector<double> bandpassFilter(std::vector<double> &wave, double fl, double fh, double fs);
        /**
         * @brief 波形频率相关性计算（波形中某频率的成分含量）
         * 
         * @param wave 波形
         * @param freq 目标频率
         * @param fs 采样率
         * @return double 
         */
        double waveFreqCor(nc::NdArray<double> wave, double freq, double fs);

        vectord digitalFilter(vectord &inputWave, vectord &preInputWave,
                              vectord &preOutputWave, double fl = 0.8, double fh = 3, double fs = 12.5, int filterOrder = 4);

        /// \brief 一阶差分信号计算
        /// \param wave 波形
        /// \return 一阶差分序列
        std::vector<double> diff1Cal(const std::vector<double>& wave);

        /// \brief 一阶中心差分计算
        /// \param wave
        /// \param rate 降采样倍率
        /// \return
        std::vector<double> diffMed1Cal(const std::vector<double>& wave, int rate = 1);

        /// 二阶中心差分计算
        /// \param wave
        /// \param rate 降采样倍率
        /// \return
        std::vector<double> diffMed2Cal(const std::vector<double>& wave, int rate = 1);

        /// 边界补偿（避免每段波形交界处间断
        /// \param wave 波形
        /// \param preBoundary 上一段的边界处波形
        /// \param compLen 补偿长度
        /// \return 补偿后波形，当前边界处波形
        std::pair<std::vector<double>, std::vector<double>> boundaryComp(const std::vector<double>& wave, const std::vector<double>& preBoundary, int compLen = 25);

        namespace butterworth {
            vectord ComputeDenCoeffs(int FilterOrder, double Lcutoff, double Ucutoff);

            vectord TrinomialMultiply(int FilterOrder, vectord b, vectord c);

            vectord ComputeNumCoeffs(int FilterOrder, double Lcutoff, double Ucutoff, vectord DenC);

            vectord ComputeLP(int FilterOrder);

            vectord ComputeHP(int FilterOrder);
        }
    }
} // namespace basic
