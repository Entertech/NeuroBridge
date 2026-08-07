#include "Common.h"
#include "FFT.hpp"
#include "Basic.hpp"
#include "Filtfilt.h"
#include <algorithm>
#include <numeric>
namespace basic
{
    namespace dsp
    {
#define PI 3.14159265358979323846
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
        nc::NdArray<double> bandSpectrumCal(nc::NdArray<double> wave, double fl, double fh, double fs, bool hammingOn, int nFFT)
        {
            
            if (nFFT == NAN)
            {
                nFFT = wave.size();
            }
            if (hammingOn)
            {
                auto hammingDouble = nc::hamming(wave.size());
                wave = nc::multiply(wave, hammingDouble);
            }
            wave = wave - nc::mean(wave).item();
            auto prefftWave = wave.toStlVector();
            auto fftWave = mathtool::fft(prefftWave, nFFT);
            auto fftNdArray = nc::NdArray(fftWave, false);
            auto specMsg = nc::abs(fftNdArray);
            auto pointL = int(round(fl / fs * nFFT));
            auto pointH = int(round(fh / fs * nFFT));
            if (pointL > pointH) 
            {
                throw std::invalid_argument("The lower cut-off frequency is higher than the upper cut-off frequency!");
            }
            auto specData = specMsg[nc::Slice(pointL, pointH)];
            return specData;
        }

        std::vector<double> bandpassFilter(std::vector<double> &wave, double fl, double fh, double fs)
        {
            int order = 3;
            std::vector<double> b;
            std::vector<double> a;
            double FrequencyBands[2] = { fl/fs*2, fh/fs*2 };
            a = butterworth::ComputeDenCoeffs(order, FrequencyBands[0], FrequencyBands[1]);

            b = butterworth::ComputeNumCoeffs(order, FrequencyBands[0], FrequencyBands[1], a);

            vectord tempWave;
            mathtool::filtfilt(b, a, wave, tempWave);

            return tempWave;
        }



        /// 单步IIR滤波（每次输入一个新的点）
        /// \param inputPoint 输入信号的最新一个值（(n)）
        /// \param inputWaveHistory 输入信号的前k个历史值（(n-1),(n-2),...,(n-k)）注意，数组左边是较新的值
        /// \param outputWaveHistory 输出信号的前k个历史值（(n-1),(n-2),...,(n-k)）注意，数组左边是较新的值
        /// \param fl 下限截止频率
        /// \param fh 上限截止频率
        /// \param fs 采样率
        /// \return
        double oneStepDigitalBandpassFilter(double inputPoint, vectord &inputWaveHistory, vectord &outputWaveHistory,
                                    double fl = 0.8, double fh = 3.0, double fs = 55.0)
        {
            double a[8] = { -7.776416664265195,  26.466100212118373, -51.48920129370114,  62.629231374135244, -48.772329091555406,
                            23.746858824415117,  -6.609334023940154,   0.8050906628019551};
            double b[9] = {2.6595845839044186e-06,  0., -1.0638338335617674e-05,  0.,
                           1.595750750342651e-05,  0., -1.0638338335617674e-05,  0., 2.6595845839044186e-06};
            double outputPoint = 0;
            outputPoint = inputPoint * b[0];
            for (int i = 0; i < 8; i++)
            {
                outputPoint += inputWaveHistory[i] * b[i+1];
                outputPoint -= outputWaveHistory[i] * a[i];
            }
            return outputPoint;
        }

        /// 数字滤波器
        /// \param inputWave 当前输入波形
        /// \param preInputWave 上一输入波形
        /// \param preOutputWave 上一输出波形
        /// \param fl 下限截止频率
        /// \param fh 上限截止频率
        /// \param fs 采样率
        /// \param filterOrder 滤波器阶数（小于上一输出波形长度的一半）
        /// \return 滤波波形
        vectord digitalFilter(vectord &inputWave, vectord &preInputWave,
                              vectord &preOutputWave, double fl, double fh, double fs, int filterOrder)
        {
            vectord outputWave;

            // Get the last 'filter_order * 2' elements and reverse them
            // Get the last 'filterOrder * 2' elements and reverse them
            std::vector<double> inputWaveHistory(preInputWave.end() - filterOrder * 2, preInputWave.end());
            std::reverse(inputWaveHistory.begin(), inputWaveHistory.end());
            std::vector<double> outputWaveHistory(preOutputWave.end() - filterOrder * 2, preOutputWave.end());
            std::reverse(outputWaveHistory.begin(), outputWaveHistory.end());
            // Assuming inputWave is std::vector<double>
            for (double inputPoint : inputWave) {
                double outputPoint = oneStepDigitalBandpassFilter(inputPoint, inputWaveHistory, outputWaveHistory, fl, fh, fs);
                outputWave.push_back(outputPoint);
                outputWaveHistory.insert(outputWaveHistory.begin(), outputPoint);
                outputWaveHistory.pop_back();
                inputWaveHistory.insert(inputWaveHistory.begin(), inputPoint);
                inputWaveHistory.pop_back();
            }


            return outputWave;
        }

        /// \brief 一阶差分信号计算
        /// \param wave 波形
        /// \return 一阶差分序列
        std::vector<double> diff1Cal(const std::vector<double>& wave)
        {
            std::vector<double> diff1(wave.size());
            for(int i = 1; i < wave.size(); i++)
                diff1[i] = wave[i] - wave[i - 1];
            diff1[0] = diff1[1];
            return diff1;
        }

        /// \brief 一阶中心差分计算
        /// \param wave
        /// \param rate 降采样倍率
        /// \return
        std::vector<double> diffMed1Cal(const std::vector<double>& wave, int rate)
        {
            std::vector<double> diff1(wave.size(), 0.0);
            for (std::size_t i = 2*rate; i < wave.size(); ++i)
                diff1[i-rate] = (wave[i] - wave[i - 2*rate]) / 2;
            return diff1;
        }

        /// 二阶中心差分计算
        /// \param wave
        /// \param rate 降采样倍率
        /// \return
        std::vector<double> diffMed2Cal(const std::vector<double>& wave, int rate)
        {
            std::vector<double> diff2(wave.size(), 0);
            for(int i = 0; i < wave.size()-4*rate; i++)
                diff2[i+2*rate] = (2 * wave[i+3*rate] + wave[i+4*rate] - wave[i] - 2 * wave[i+rate]) / 8;


            return diff2;
        }

        /// 边界补偿（避免每段波形交界处间断
        /// \param wave 波形
        /// \param preBoundary 上一段的边界处波形
        /// \param compLen 补偿长度
        /// \return 补偿后波形，当前边界处波形
        std::pair<std::vector<double>, std::vector<double>> boundaryComp(const std::vector<double>& wave, const std::vector<double>& preBoundary, int compLen)
        {
            // Concatenate preBoundary and wave
            std::vector<double> compWave(preBoundary);
            compWave.insert(compWave.end(), wave.begin(), wave.end());
            double boundary = (preBoundary.back() + wave[0]) / 2.0;
            for (int i = 0; i < compLen; ++i) {
                double weight = static_cast<double>(i) / compLen;
                compWave[preBoundary.size() + i] = compWave[preBoundary.size() + i] * weight + boundary * (1 - weight);  // Right neighborhood
                compWave[preBoundary.size() - i] = compWave[preBoundary.size() - i] * weight + boundary * (1 - weight);  // Left neighborhood
            }
            std::vector<double> outputWave(compWave.begin(), compWave.begin() + wave.size());
            std::vector<double> curBoundary(compWave.end() - preBoundary.size(), compWave.end());
            return std::make_pair(outputWave, curBoundary);
        }

        double waveFreqCor(nc::NdArray<double> wave, double freq, double fs)
        {
            auto nWave = wave.toStlVector();
            auto waveMean = mathtool::mean(nWave);
            for (auto &e : nWave)
            {
                e = e - waveMean;
            }
            auto t = nc::linspace(0., wave.size()/fs, wave.size());
            for (auto &e : t)
            {
                e = 2*M_PI*freq*e;
            }
            auto sinWave = nc::sin(t).toStlVector();
            auto cosWave = nc::cos(t).toStlVector();

            auto corSin = mathtool::cov(sinWave, nWave) / (mathtool::stdv(sinWave)*mathtool::stdv(nWave));
            auto corCos = mathtool::cov(cosWave, nWave) / (mathtool::stdv(cosWave)*mathtool::stdv(nWave));
            auto cor = nc::sqrt(std::pow(corSin, 2)+std::pow(corCos, 2));
            cor = std::max(std::min(cor, 1.0), 0.0);
            return cor;
        }
        namespace butterworth {
            vectord ComputeDenCoeffs(int FilterOrder, double Lcutoff, double Ucutoff) {
                int k;            // loop variables
                double theta;     // PI * (Ucutoff - Lcutoff) / 2.0
                double cp;        // cosine of phi
                double st;        // sine of theta
                double ct;        // cosine of theta
                double s2t;       // sine of 2*theta
                double c2t;       // cosine 0f 2*theta
                vectord RCoeffs(2 * FilterOrder);     // z^-2 coefficients
                vectord TCoeffs(2 * FilterOrder);     // z^-1 coefficients
                vectord DenomCoeffs;     // dk coefficients
                double PoleAngle;      // pole angle
                double SinPoleAngle;     // sine of pole angle
                double CosPoleAngle;     // cosine of pole angle
                double a;         // workspace variables

                cp = cos(PI * (Ucutoff + Lcutoff) / 2.0);
                theta = PI * (Ucutoff - Lcutoff) / 2.0;
                st = sin(theta);
                ct = cos(theta);
                s2t = 2.0 * st * ct;        // sine of 2*theta
                c2t = 2.0 * ct * ct - 1.0;  // cosine of 2*theta

                for (k = 0; k < FilterOrder; ++k) {
                    PoleAngle = PI * (double) (2 * k + 1) / (double) (2 * FilterOrder);
                    SinPoleAngle = sin(PoleAngle);
                    CosPoleAngle = cos(PoleAngle);
                    a = 1.0 + s2t * SinPoleAngle;
                    RCoeffs[2 * k] = c2t / a;
                    RCoeffs[2 * k + 1] = s2t * CosPoleAngle / a;
                    TCoeffs[2 * k] = -2.0 * cp * (ct + st * SinPoleAngle) / a;
                    TCoeffs[2 * k + 1] = -2.0 * cp * st * CosPoleAngle / a;
                }

                DenomCoeffs = TrinomialMultiply(FilterOrder, TCoeffs, RCoeffs);

                DenomCoeffs[1] = DenomCoeffs[0];
                DenomCoeffs[0] = 1.0;
                for (k = 3; k <= 2 * FilterOrder; ++k)
                    DenomCoeffs[k] = DenomCoeffs[2 * k - 2];

                for (size_t i = DenomCoeffs.size() - 1; i > FilterOrder * 2 + 1; i--)
                    DenomCoeffs.pop_back();

                return DenomCoeffs;
            }

            vectord TrinomialMultiply(int FilterOrder, vectord b, vectord c) {
                int i, j;
                vectord RetVal(4 * FilterOrder);

                RetVal[2] = c[0];
                RetVal[3] = c[1];
                RetVal[0] = b[0];
                RetVal[1] = b[1];

                for (i = 1; i < FilterOrder; ++i) {
                    RetVal[2 * (2 * i + 1)] +=
                            c[2 * i] * RetVal[2 * (2 * i - 1)] - c[2 * i + 1] * RetVal[2 * (2 * i - 1) + 1];
                    RetVal[2 * (2 * i + 1) + 1] +=
                            c[2 * i] * RetVal[2 * (2 * i - 1) + 1] + c[2 * i + 1] * RetVal[2 * (2 * i - 1)];

                    for (j = 2 * i; j > 1; --j) {
                        RetVal[2 * j] += b[2 * i] * RetVal[2 * (j - 1)] - b[2 * i + 1] * RetVal[2 * (j - 1) + 1] +
                                         c[2 * i] * RetVal[2 * (j - 2)] - c[2 * i + 1] * RetVal[2 * (j - 2) + 1];
                        RetVal[2 * j + 1] += b[2 * i] * RetVal[2 * (j - 1) + 1] + b[2 * i + 1] * RetVal[2 * (j - 1)] +
                                             c[2 * i] * RetVal[2 * (j - 2) + 1] + c[2 * i + 1] * RetVal[2 * (j - 2)];
                    }

                    RetVal[2] += b[2 * i] * RetVal[0] - b[2 * i + 1] * RetVal[1] + c[2 * i];
                    RetVal[3] += b[2 * i] * RetVal[1] + b[2 * i + 1] * RetVal[0] + c[2 * i + 1];
                    RetVal[0] += b[2 * i];
                    RetVal[1] += b[2 * i + 1];
                }

                return RetVal;
            }

            vectord ComputeNumCoeffs(int FilterOrder, double Lcutoff, double Ucutoff, vectord DenC) {
                vectord TCoeffs;
                vectord NumCoeffs(2 * FilterOrder + 1);
                std::vector<std::complex<double>> NormalizedKernel(2 * FilterOrder + 1);

                vectord Numbers;
                for (double n = 0; n < FilterOrder * 2 + 1; n++)
                    Numbers.push_back(n);
                int i;

                TCoeffs = ComputeHP(FilterOrder);

                for (i = 0; i < FilterOrder; ++i) {
                    NumCoeffs[2 * i] = TCoeffs[i];
                    NumCoeffs[2 * i + 1] = 0.0;
                }
                NumCoeffs[2 * FilterOrder] = TCoeffs[FilterOrder];

                double cp[2];
                double Bw, Wn;
                cp[0] = 2 * 2.0 * tan(PI * Lcutoff / 2.0);
                cp[1] = 2 * 2.0 * tan(PI * Ucutoff / 2.0);

                Bw = cp[1] - cp[0];
                //center frequency
                Wn = sqrt(cp[0] * cp[1]);
                Wn = 2 * atan2(Wn, 4);
                double kern;
                const std::complex<double> result = std::complex<double>(-1, 0);

                for (int k = 0; k < FilterOrder * 2 + 1; k++) {
                    NormalizedKernel[k] = std::exp(-sqrt(result) * Wn * Numbers[k]);
                }
                double b = 0;
                double den = 0;
                for (int d = 0; d < FilterOrder * 2 + 1; d++) {
                    b += real(NormalizedKernel[d] * NumCoeffs[d]);
                    den += real(NormalizedKernel[d] * DenC[d]);
                }
                for (int c = 0; c < FilterOrder * 2 + 1; c++) {
                    NumCoeffs[c] = (NumCoeffs[c] * den) / b;
                }

                for (size_t i = NumCoeffs.size() - 1; i > FilterOrder * 2 + 1; i--)
                    NumCoeffs.pop_back();

                return NumCoeffs;
            }

            vectord ComputeLP(int FilterOrder) {
                vectord NumCoeffs(FilterOrder + 1);
                int m;
                int i;

                NumCoeffs[0] = 1;
                NumCoeffs[1] = FilterOrder;
                m = FilterOrder / 2;
                for (i = 2; i <= m; ++i) {
                    NumCoeffs[i] = (double) (FilterOrder - i + 1) * NumCoeffs[i - 1] / i;
                    NumCoeffs[FilterOrder - i] = NumCoeffs[i];
                }
                NumCoeffs[FilterOrder - 1] = FilterOrder;
                NumCoeffs[FilterOrder] = 1;

                return NumCoeffs;
            }

            vectord ComputeHP(int FilterOrder) {
                vectord NumCoeffs;
                int i;

                NumCoeffs = ComputeLP(FilterOrder);

                for (i = 0; i <= FilterOrder; ++i)
                    if (i % 2) NumCoeffs[i] = -NumCoeffs[i];

                return NumCoeffs;
            }
        }
    } // namespace dsp
    
} // namespace basic
